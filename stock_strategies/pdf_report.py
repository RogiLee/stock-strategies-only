"""PDF attachment after the original text report; never evaluates a strategy."""
import html
import math
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

STYLE = Path(__file__).with_name('report_style.css')
ORDER = ['2330', '006208', '00981A', '0050', '2308', '009828']


def number(value, percent=False):
    if value is None or value == '':
        return '—'
    try:
        n = float(value)
    except (TypeError, ValueError):
        return '—'
    if not math.isfinite(n):
        return '—'
    return f'{n * 100:.1f}%' if percent else f'{n:,.2f}'.rstrip('0').rstrip('.')


def text(value):
    if isinstance(value, (list, tuple)):
        value = ' / '.join(map(str, value))
    return html.escape(str(value if value is not None else ''), quote=True)


def latest_batch(signals):
    days = [str(r.get('date', '')) for r in signals if r.get('date')]
    day = max(days, default='')
    seen = set()
    result = []
    for row in signals:
        sid = str(row.get('stock_id', ''))
        if (not day or str(row.get('date', '')) == day) and sid not in seen:
            seen.add(sid)
            result.append(row)
    # Sheet reads are newest-first; retries on the same date must not duplicate PDF cards.
    # Preserve each ID as text; do not fabricate a zero for missing numeric data.
    return sorted(result, key=lambda r: ORDER.index(str(r['stock_id'])) if str(r['stock_id']) in ORDER else len(ORDER))


def stock_card(row):
    c = row.get('components') or {}
    def val(flat, nested=None):
        return row.get(flat, c.get(nested or flat, ''))
    action = str(row.get('action', 'ERROR')).upper()
    badge = action.lower() if action in ('BUY', 'WATCH', 'SKIP') else 'skip'
    label = {'BUY': '符合條件', 'WATCH': '觀察', 'SKIP': '略過'}.get(action, '資料異常')
    score = number(row.get('signal_score'))
    bar = '' if score == '—' else f'<div class="score-track"><i style="width:{min(100, max(0, float(row["signal_score"])))}%"></i></div>'
    prices = ''.join(f'<div><dt>{title}</dt><dd>{number(row.get(key))}</dd></div>' for title, key in [('原版參考價', 'entry_price'), ('停損參考', 'stop_loss_price'), ('目標參考', 'target_price')])
    if number(row.get('entry_price')) == '—':
        prices = '<div class="missing">原版未提供有效價格或評分；詳見風險註記。</div>'
    risk = row.get('risk_notes')
    risk_html = f'<div class="risk">{text(risk)}</div>' if risk else '<div class="quiet">原版未附加風險註記</div>'
    rr = row.get('rr_ratio', row.get('risk_reward_ratio'))
    position = row.get('position_pct', row.get('position_size_pct'))
    metrics = [('歷史回測勝率', number(val('winrate', 'backtest_winrate'), True)), ('回測樣本', number(val('samples', 'backtest_samples'))), ('風報比', '1 : ' + number(rr) if number(rr) != '—' else '—'), ('原版建議部位', number(position) + '%' if number(position) != '—' else '—')]
    return f'''<article class="stock"><div class="card-top"><div><div class="code">{text(row.get('stock_id'))}</div><h3>{text(row.get('name'))}</h3></div><span class="badge {badge}">{text(action)} · {label}</span></div>
<div class="score"><span>綜合評分</span><strong>{score}<small>{'/ 100' if score != '—' else '資料不足'}</small></strong></div>{bar}
<dl class="prices">{prices}</dl><div class="signal"><span>技術觸發</span><p>{text(val('tech_signals')) or '未記錄觸發訊號'}</p></div>
<dl class="metrics">{''.join(f'<div><dt>{title}</dt><dd>{value}</dd></div>' for title, value in metrics)}</dl>{risk_html}<p class="card-foot">參考價不代表已執行交易；WATCH／SKIP 不是買進指令。</p></article>'''


def render_html(kind, signals, messages, market=None, night=None, report_date=None):
    if kind not in ('daily', 'premarket'):
        raise ValueError('Unknown report kind')
    if not messages or not all(isinstance(m, str) for m in messages):
        raise ValueError('Original report text is required')
    report_date = report_date or datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    batch = latest_batch(signals)
    source_day = str(batch[0].get('date', '')) if batch else ''
    counts = Counter(str(r.get('action', 'ERROR')).upper() for r in batch)
    heading = '收盤分析' if kind == 'daily' else '盤前夜盤快報'
    stats = ''.join(f'<div class="stat {cls}"><span>{title}</span><b>{count}</b><small>{note}</small></div>' for cls, title, count, note in [('', 'TRACKING / 本批訊號', len(batch), '原版資料'), ('buy', 'BUY / 買進', counts['BUY'], '原版訊號'), ('watch', 'WATCH / 觀察', counts['WATCH'], '仍需等待條件'), ('', 'SKIP / 略過', counts['SKIP'] + counts['ERROR'], '包含資料不足或錯誤')])
    market = market or {}
    market_html = f'<div class="big">{number(market.get("close"))} <small>點</small></div><p>{text(market.get("note"))}</p>' if market else '<p>本次為盤前回報，沿用已儲存的收盤訊號，沒有重新進行個股評分或取得新的大盤日線。</p>'
    if night:
        pct = number(night.get('pct'))
        if pct != '—' and float(night['pct']) > 0:
            pct = '+' + pct
        change = pct + '%' if pct != '—' else '—'
        night_html = f'<div class="big" style="color:{"#20765c" if float(night.get("pct", 0)) >= 0 else "#b44b56"}">{change} <small>{text(night.get("label"))}</small></div><p>夜盤日期：{text(night.get("date"))}</p>'
        night_detail = f'<div class="night-score">{change}<small>資料日期 {text(night.get("date"))}</small></div><div><h3>{text(night.get("label"))}</h3><p>{text(night.get("direction"))}</p><p>近月收 {number(night.get("close"))} ｜ 量 {number(night.get("volume"))} ｜ 漲跌 {number(night.get("spread"))} 點</p></div>'
    else:
        night_html = '<div class="big">—</div><p>原版本次未取得夜盤資料，不補造數字。</p>'
        night_detail = '<p>本次未取得夜盤資料；以原版文字回報記錄為準。</p>'
    verbatim = ''.join(f'<article class="verbatim"><h3>原版回報 · 第 {i} 則</h3><div class="verbatim-text">{text(message)}</div></article>' for i, message in enumerate(messages, 1))
    cards = ''.join(stock_card(r) for r in batch) or '<p class="empty">本次沒有可供列示的個股訊號。完整回報與缺資料說明仍保留於後方。</p>'
    page = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>{report_date} {heading}｜六檔台股閱讀報告</title><style>{STYLE.read_text()}</style></head><body>
<header class="mast"><div class="eyebrow">STOCK STRATEGIES / READING EDITION</div><div class="hero"><div><h1>把今天的訊號，<br>放在同一頁。</h1><p class="sub">六檔台股・{heading}<br>同批原版結果的 PDF 閱讀版，不重新計算策略。</p></div><div class="date">回報日期（台灣）<b>{report_date}</b></div></div></header>
<section class="section"><div class="section-head"><h2>市場總覽</h2><p>個股訊號來源日：{text(source_day) or '未提供'}；回報日與來源日分別標示，不視為即時行情。</p></div><div class="stats">{stats}</div><div class="market"><div class="panel"><h3>加權指數｜原版大盤濾鏡</h3>{market_html}</div><div class="panel"><h3>台指期夜盤｜原版情緒濾鏡</h3>{night_html}</div></div><div class="callout">本頁只整理原版結果。夜盤順風不會自動將 WATCH 或 SKIP 改成 BUY。</div></section>
<section class="section"><div class="section-head"><h2>夜盤與盤前摘要</h2><p>依同次執行取得的夜盤資料呈現；不補算或改寫方向預判。</p></div><div class="panel night-grid">{night_detail}</div></section>
<section class="section" id="stocks"><div class="section-head"><h2>六檔結果</h2><p>個股訊號來源日 {text(source_day) or '未提供'} ｜ 價格單位：新台幣 ｜ 卡片後附本次原版文字回報全文</p></div><div class="grid">{cards}</div></section>
<section class="section" id="sources"><div class="section-head"><h2>來源與閱讀限制</h2><p>文字回報在前，PDF 隨後送出；使用相同資料，不另跑一次策略。</p></div><div class="panel"><ul class="note-list"><li>數字、訊號與風險註記均取自本次原版流程。盤前卡片沿用原版儲存的最近一批收盤結果，日期如上；完整盤前文字仍保留於後方。</li><li>缺值顯示「—」。ETF 基本面門檻、回測參數與建議部位不作調整。單檔部位欄位不代表應同時配置全部標的。</li><li>回測勝率不保證未來結果；成本、滑價、歷史價格調整及短歷史仍有原版限制。</li><li>後附原版全文，保留趨勢、均線、量能、操作建議及有提供時的績效訊息；PDF 不刪改原版判斷。</li></ul></div></section>
<section class="section" id="verbatim"><div class="section-head"><h2>本次原版文字回報全文</h2><p>下列內容與先前傳送的文字相同；保留原版符號與標記，避免轉排遺漏。</p></div>{verbatim}</section><footer>小胖亨整理 · stock-strategies-only 原版結果閱讀版<br>僅供研究參考，不構成投資建議，不涉及自動下單。</footer></body></html>'''
    # No credentials or arbitrary environment variables are placed in the document.
    for key in ('FINMIND_TOKEN', 'TELEGRAM_BOT_TOKEN', 'GOOGLE_CREDS_JSON'):
        secret = os.environ.get(key, '')
        if len(secret) >= 8:
            page = page.replace(secret, '[已隱藏]').replace(html.escape(secret, quote=True), '[已隱藏]')
    return page


def make_pdf(html_body, output):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        # On GitHub use installed Playwright Chromium. Local smoke tests may use Chrome.
        channel = os.environ.get('PDF_BROWSER_CHANNEL')
        browser = p.chromium.launch(headless=True, **({'channel': channel} if channel else {}))
        page = browser.new_page(viewport={'width': 1100, 'height': 1000})
        page.route(re.compile(r'^https?://'), lambda route: route.abort())
        page.set_content(html_body, wait_until='load')
        page.emulate_media(media='print')
        page.pdf(path=str(output), format='A4', print_background=True, prefer_css_page_size=True,
                 display_header_footer=True, header_template='<span></span>',
                 footer_template='<div style="width:100%;font-size:9px;color:#9298a4;padding:0 13mm;display:flex;justify-content:space-between"><span>六檔台股・原版報告閱讀版</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
        browser.close()
    if not Path(output).read_bytes().startswith(b'%PDF-'):
        raise RuntimeError('Invalid PDF output')


def send_pdf_report(kind, signals, messages, market=None, night=None):
    if os.environ.get('PDF_REPORT_ENABLED', '').lower() != 'true':
        return
    day = datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    label = '收盤分析' if kind == 'daily' else '盤前夜盤快報'
    body = render_html(kind, signals, messages, market, night, day)
    with tempfile.TemporaryDirectory(prefix='stock-pdf-') as directory:
        output = Path(directory) / f'台股報告_{day}_{label}.pdf'
        make_pdf(body, output)
        with output.open('rb') as f:
            response = requests.post('https://api.telegram.org/bot' + os.environ['TELEGRAM_BOT_TOKEN'] + '/sendDocument',
                                     data={'chat_id': os.environ['TELEGRAM_CHAT_ID'], 'caption': f'{day} {label}｜原版內容 PDF 閱讀版'},
                                     files={'document': (output.name, f, 'application/pdf')}, timeout=90)
        if not response.ok:
            raise RuntimeError(f'PDF Telegram delivery failed: HTTP {response.status_code}')
        data = response.json()
        if not data.get('ok'):
            raise RuntimeError('Telegram rejected PDF attachment')
        result = data['result']
        print(f"PDF_DELIVERED kind={kind} records={len(latest_batch(signals))} texts={len(messages)} message_id={result['message_id']} file_bytes={result.get('document', {}).get('file_size', output.stat().st_size)}")
