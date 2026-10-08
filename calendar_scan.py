from datetime import date
from html.parser import HTMLParser
import re
import time
URL = "https://dshinsei.e-kanagawa.lg.jp/140007-u/reserve/offerList_detail?tempSeq=45604&accessFrom=offerList"
NEXT = "2週後のカレンダーページへ"
PREVIOUS = "2週前のカレンダーページへ"

class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.table = False
        self.row = None
        self.cell = None
        self.cells = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'table' and a.get('id') == 'TBL':
            self.table = True
        if not self.table:
            return
        if tag == 'tr':
            self.row = a.get('id')
            self.cells[self.row] = []
        if tag == 'td' and self.row:
            self.cell = {'text': '', 'labels': []}
        if self.cell is not None and a.get('role') == 'img':
            self.cell['labels'].append(a.get('aria-label', '').strip())

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.cells[self.row].append(self.cell)
            self.cell = None
        if tag == 'tr':
            self.row = None
        if tag == 'table':
            self.table = False


def parse_calendar(html):
    p = CalendarParser()
    p.feed(html)
    headers = p.cells.get('height_headday', [])
    cells = p.cells.get('height_auto_大型自動二輪', [])
    year_match = re.search(r'(\d{4})年', ''.join(c['text'] for c in p.cells.get('height_head', [])))
    if not year_match or not headers or len(headers) != len(cells):
        raise ValueError('日历结构不完整，不能判定为空位为零')
    year = int(year_match[1])
    result = {}
    prev_month = None
    for header, cell in zip(headers, cells):
        match = re.search(r'(\d{1,2})/(\d{1,2})', header['text'])
        if not match:
            raise ValueError('无法识别日期')
        month, day = map(int, match.groups())
        if prev_month is not None and month < prev_month:
            year += 1
        prev_month = month
        dt = date(year, month, day)
        labels = cell['labels']
        if len(labels) != 1 or labels[0] not in {'予約可能', '空き無', '時間外', '選択中'}:
            raise ValueError('无法识别预约状态')
        # A selected marker is not proof that a reservation is available.
        result[dt] = labels[0] == '予約可能'
    days = sorted(result)
    if len(days) != len(headers) or any((b-a).days != 1 for a, b in zip(days, days[1:])):
        raise ValueError('日历日期不连续')
    return result


class CalendarTimeout(TimeoutError):
    pass


class CalendarScanError(RuntimeError):
    """Fixed, public-safe calendar diagnostics; never contains page content."""
    pass


def scan(start, end, headed=False):
    for attempt in range(2):
        try:
            return _scan_once(start, end, headed)
        except CalendarTimeout:
            if attempt == 1:
                raise
            print('日历读取超时；5秒后重新打开网页并完整检查一次。')
            time.sleep(5)


def _scan_once(start, end, headed=False):
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    try:
        return _scan_browser(start, end, headed, sync_playwright)
    except PlaywrightTimeout:
        raise CalendarTimeout('日历读取超时，完整扫描未完成') from None


def _scan_browser(start, end, headed, sync_playwright):
    all_days = {}
    effective_start = start
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        try:
            page = browser.new_page(locale='ja-JP', timezone_id='Asia/Tokyo')
            page.set_default_timeout(30000)
            response = page.goto(URL, wait_until='domcontentloaded', timeout=60000)
            if response is None or response.status >= 400:
                raise CalendarScanError('预约网页访问失败')
            for page_index in range(16):
                page.locator('[id="height_auto_大型自動二輪"]').wait_for(state='attached')
                block = parse_calendar('<table id="TBL">'+page.locator('#TBL').inner_html()+'</table>')
                if page_index == 0 and min(block) > start:
                    previous = page.get_by_role('button', name=PREVIOUS, exact=True)
                    if previous.is_enabled():
                        raise CalendarScanError('首页晚于监控起点，但向前翻页仍可用；不能跳过此前日期')
                    effective_start = min(block)
                    print('官网向前翻页已禁用；本轮起始日期调整为 '+effective_start.isoformat())
                    if effective_start > end:
                        print('官网最早显示日期已超过截止日期；本轮没有可检查日期。')
                        return []
                all_days.update(block)
                if max(block) >= end:
                    break
                button = page.get_by_role('button', name=NEXT, exact=True)
                if not button.is_enabled():
                    raise CalendarScanError('网页暂未提供全部目标日期，不能完成本轮检查')
                old_first = min(block)
                button.click()
                page.wait_for_function('old => {const c=document.querySelector("#height_headday td"); const m=c && c.innerText.match(/(\\d{1,2})\\/(\\d{1,2})/); return m && (Number(m[1])+"/"+Number(m[2])) !== old;}', arg=page_date_text(old_first))
                time.sleep(1)
            else:
                raise CalendarScanError('日历翻页超过预期范围')
        finally:
            browser.close()
    expected = (end-effective_start).days + 1
    covered = {dt for dt in all_days if effective_start <= dt <= end}
    if len(covered) != expected:
        raise CalendarScanError('未完整覆盖目标日期，本轮状态不会写入')
    return sorted(dt.isoformat() for dt in covered if all_days[dt])


def page_date_text(dt):
    # Compare the numeric portion of the header rather than weekday text.
    return f'{dt.month}/{dt.day}'

