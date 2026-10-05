"""GitHub Actions watcher. Personal contact details come only from Secrets."""
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from calendar_scan import scan, URL

ROOT = Path(__file__).resolve().parent
JST = ZoneInfo('Asia/Tokyo')


class ApiFailure(Exception):
    def __init__(self, code, accepted=False, reason='unknown'):
        self.code = code
        self.accepted = accepted
        self.reason = reason
        super().__init__(f'HTTP {code}')


def request_json(url, token, method='GET', payload=None, headers=None):
    hdr = {'Authorization': 'Bearer '+token, 'Accept': 'application/json',
           'Content-Type': 'application/json', 'User-Agent': 'KanagawaReservationMonitor'}
    hdr.update(headers or {})
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(url, data=data, headers=hdr, method=method)
    try:
        with urlopen(req, timeout=45) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as exc:
        accepted = bool(exc.headers.get('x-line-accepted-request-id'))
        # Report only fixed diagnostic categories, never raw provider bodies.
        reason = 'unknown'
        try:
            problem = json.loads(exc.read(65536))
            known = {'missing_permission', 'forbidden', 'unknown_api_key',
                     'api_key_expired', 'message_rejected', 'inbox_paused',
                     'rate_limit_exceeded', 'limit_exceeded', 'service_unavailable',
                     'internal_error', 'domain_not_verified', 'unauthorized'}
            if problem.get('code') in known:
                reason = problem['code']
            hint = (str(problem.get('message', ''))+' '+str(problem.get('fix', ''))).lower()
            if reason == 'message_rejected':
                if 'suspend' in hint:
                    reason = 'account_suspended'
                elif 'allow list' in hint or 'allowlist' in hint or 'verification' in hint:
                    reason = 'recipient_allowlist_or_verification'
                elif 'block' in hint or 'suppress' in hint:
                    reason = 'recipient_blocked'
        except (ValueError, TypeError, AttributeError):
            pass
        raise ApiFailure(exc.code, accepted, reason) from None


class GitHubFiles:
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.token = os.environ['GITHUB_TOKEN']
        self.branch = os.environ.get('GITHUB_REF_NAME', 'main')
        self.shas = {}

    def read(self, path):
        url = 'https://api.github.com/repos/'+self.repo+'/contents/'+quote(path, safe='/')
        data = request_json(url+'?ref='+quote(self.branch, safe=''), self.token)
        self.shas[path] = data['sha']
        return json.loads(base64.b64decode(data['content']))

    def write(self, path, value):
        # Optimistic SHA guard: never overwrite a concurrent edit.
        url = 'https://api.github.com/repos/'+self.repo+'/contents/'+quote(path, safe='/')
        encoded = base64.b64encode((json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()).decode()
        payload = {'message': 'Update monitor settings' if path == 'settings.json' else 'Record notification status',
                   'content': encoded, 'sha': self.shas[path], 'branch': self.branch}
        data = request_json(url, self.token, 'PUT', payload,
                            {'Accept': 'application/vnd.github+json'})
        self.shas[path] = data['content']['sha']


def targets(include_line=True):
    required = ['AGENTMAIL_API_KEY', 'AGENTMAIL_INBOX_ID', 'NOTIFY_EMAILS']
    if include_line:
        required += ['LINE_CHANNEL_ACCESS_TOKEN', 'LINE_USER_ID']
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError('未设置：'+', '.join(missing))
    recipients = json.loads(os.environ['NOTIFY_EMAILS'])
    if not isinstance(recipients, list) or len(recipients) != 2 or any(not isinstance(r, str) or '@' not in r for r in recipients):
        raise ValueError('NOTIFY_EMAILS必须是包含两个邮箱地址的JSON列表')
    if len(set(recipients)) != 2:
        raise ValueError('两个收件地址不能重复')
    return [('email', r) for r in recipients]+([('line', os.environ['LINE_USER_ID'])] if include_line else [])


def target_id(kind, recipient):
    return kind+':'+hashlib.sha256(recipient.encode()).hexdigest()


def send(kind, recipient, body, retry_key):
    if kind == 'email':
        inbox = quote(os.environ['AGENTMAIL_INBOX_ID'], safe='')
        return request_json('https://api.agentmail.to/v0/inboxes/'+inbox+'/messages/send',
                            os.environ['AGENTMAIL_API_KEY'], 'POST',
                            {'to': [recipient], 'subject': '神奈川大型二轮预约通知', 'text': body})
    try:
        return request_json('https://api.line.me/v2/bot/message/push',
                            os.environ['LINE_CHANNEL_ACCESS_TOKEN'], 'POST',
                            {'to': recipient, 'messages': [{'type': 'text', 'text': body}]},
                            {'X-Line-Retry-Key': retry_key})
    except ApiFailure as exc:
        if exc.code == 409 and exc.accepted:
            return {'already_accepted': True}
        raise


def make_body(days, stamp):
    return ('神奈川大型自動二輪出现可预约日期\n\n'+'\n'.join(days)+
            '\n\n检查时间（日本时间）：'+stamp+'\n预约链接：'+URL+
            '\n\n名额可能随时变化，请打开官网确认。每个日期只通知一次；不会自动提交预约。')


def notify_new(state, available, destinations, persist, sender=send, now=None):
    now = now or datetime.now(JST)
    ledger = state.setdefault('deliveries', {})
    failed = False
    for kind, recipient in destinations:
        key = target_id(kind, recipient)
        entries = ledger.setdefault(key, [])
        recorded = {d for entry in entries for d in entry['dates']}
        # A pending email has an ambiguous send outcome after interruption.
        # Never resend it automatically; it must first be reconciled in AgentMail.
        for entry in entries:
            if entry['status'] == 'pending' and kind == 'email':
                entry['status'] = 'uncertain'
                persist(state)
                failed = True
        batches = [e for e in entries if e['status'] == 'pending' and kind == 'line']
        new = sorted(set(available)-recorded)
        if new:
            entry = {'dates': new, 'status': 'pending', 'created_at': now.isoformat(),
                     'retry_key': str(uuid.uuid4()), 'body': make_body(new, now.isoformat())}
            entries.append(entry)
            persist(state)  # Reserve dates before making an external request.
            batches.append(entry)
        for entry in batches:
            if kind == 'line' and now-datetime.fromisoformat(entry['created_at']) >= timedelta(hours=23):
                entry['status'] = 'uncertain'
                persist(state)
                failed = True
                continue
            try:
                sender(kind, recipient, entry['body'], entry['retry_key'])
            except ApiFailure as exc:
                if 400 <= exc.code < 500 and exc.code != 409:
                    # Explicit rejection: no accepted send, so retry on a future
                    # scan after quota, configuration, or authentication is fixed.
                    entries.remove(entry)
                elif kind == 'email':
                    entry['status'] = 'uncertain'
                persist(state)
                failed = True
                print(kind+': 通知未完成（HTTP '+str(exc.code)+'；原因 '+exc.reason+'）')
                continue
            except (URLError, TimeoutError, OSError):
                if kind == 'email':
                    entry['status'] = 'uncertain'
                persist(state)
                failed = True
                print(kind+': 网络异常，已保留防重复记录')
                continue
            entry['status'] = 'sent'
            entry['accepted_at'] = now.isoformat()
            persist(state)
            print(kind+': 新空位通知已被服务接受，日期数 '+str(len(entry['dates'])))
        if any(e['status'] == 'uncertain' for e in entries):
            failed = True
            print(kind+': 存在待人工核实的通知记录')
    return not failed


def main():
    mode = os.environ.get('MONITOR_MODE', 'check')
    files = GitHubFiles()
    settings = files.read('settings.json')
    if mode == 'set_range':
        end = date.fromisoformat(os.environ.get('NEW_END_DATE', ''))
        if end < datetime.now(JST).date():
            raise ValueError('截止日期不能早于日本时间当日')
        settings['end_date'] = end.isoformat()
        files.write('settings.json', settings)
        print('截止日期已更新为 '+end.isoformat()+'；已通知记录保留')
        return 0
    if mode in {'enable', 'pause'}:
        if mode == 'enable':
            targets(include_line=settings.get('line_enabled', False))
        settings['enabled'] = mode == 'enable'
        files.write('settings.json', settings)
        print('已启用' if settings['enabled'] else '已暂停')
        return 0
    if mode in {'test', 'email_test'}:
        include_line = mode == 'test' and settings.get('line_enabled', False)
        if mode == 'test' and not include_line:
            print('LINE通知已关闭；本次只测试两个邮件地址。')
        for kind, recipient in targets(include_line=include_line):
            send(kind, recipient, '神奈川大型二轮预约监控：测试通知。\n这不是空位通知。\n截止日期：'+settings['end_date'], str(uuid.uuid4()))
            print(kind+': 测试通知已被服务接受')
        return 0
    if mode != 'check':
        raise ValueError('未知操作模式')
    if not settings['enabled']:
        print('监控尚未启用，未检查网页或发送通知。')
        return 0
    today = datetime.now(JST).date()
    end = date.fromisoformat(settings['end_date'])
    if today > end:
        settings['enabled'] = False
        files.write('settings.json', settings)
        print('已过截止日期，自动暂停。')
        return 0
    destinations = targets(include_line=settings.get('line_enabled', False))
    available = scan(today, end)
    print('本轮完整检查完成，范围内空位日期数：'+str(len(available)))
    print('可预约日期：'+(', '.join(available) or '无'))
    state = files.read('state.json')
    return 0 if notify_new(state, available, destinations, lambda s: files.write('state.json', s)) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Never emit raw HTTP errors, contact addresses or tokens to public logs.
        print('本轮未完成：'+type(exc).__name__+'。请检查连接、配置及运行记录。')
        sys.exit(1)
