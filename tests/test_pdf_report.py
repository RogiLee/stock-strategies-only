from unittest.mock import Mock, patch

import pytest

from stock_strategies import pdf_report as pdf
from stock_strategies.sheet import read_latest_signals


def row(sid='006208', day='2026-10-21', **extra):
    return dict(date=day, stock_id=sid, name='富邦台50', action='WATCH', signal_score=53.3, entry_price=247.6, **extra)


def test_latest_batch_deduplicates_newest_first_without_changing_ids():
    newest = row(signal='new')
    result = pdf.latest_batch([newest, row(signal='old'), row('0050'), row('2330', day='2026-10-20')])
    assert [r['stock_id'] for r in result] == ['006208', '0050']
    assert result[0]['signal'] == 'new'


def test_daily_nested_and_premarket_flat_records_match():
    base = row()
    nested = {**base, 'components': {'backtest_winrate': 0.748, 'backtest_samples': 234, 'tech_signals': ['布林下軌反彈']}, 'risk_reward_ratio': 1.25, 'position_size_pct': 20}
    flat = {**base, 'winrate': 0.748, 'samples': 234, 'tech_signals': '布林下軌反彈', 'rr_ratio': 1.25, 'position_pct': 20}
    assert pdf.stock_card(nested) == pdf.stock_card(flat)


def test_render_preserves_every_original_message_and_escapes_html():
    messages = ['原版市場\n*未改訊號*', '原版操作建議\n<script>bad</script>', '量價深度解析全文']
    doc = pdf.render_html('daily', [row()], messages, report_date='2026-10-21')
    assert all(pdf.text(m) in doc for m in messages)
    assert '<script>bad</script>' not in doc
    assert '006208' in doc and '2026-10-21' in doc


def test_missing_data_never_becomes_zero():
    doc = pdf.render_html('premarket', [row('009828', entry_price_missing=True) | {'entry_price': '', 'signal_score': '', 'action': 'SKIP', 'risk_notes': '價格資料不足'}], ['原版盤前內容'])
    assert '價格資料不足' in doc and '資料不足' in doc and '>—<' in doc
    assert pdf.number(None) == pdf.number('') == pdf.number(float('nan')) == '—'


def test_no_data_and_no_night_still_render_honestly():
    doc = pdf.render_html('premarket', [], ['夜盤資料暫時取不到'])
    assert '本次沒有可供列示的個股訊號' in doc
    assert '本次未取得夜盤資料' in doc
    assert '未提供' in doc


def test_known_credentials_are_never_embedded(monkeypatch):
    monkeypatch.setenv('FINMIND_TOKEN', 'secret-for-test-only')
    doc = pdf.render_html('daily', [], ['error secret-for-test-only'])
    assert 'secret-for-test-only' not in doc and '[已隱藏]' in doc


def test_pdf_is_opt_in(monkeypatch):
    monkeypatch.delenv('PDF_REPORT_ENABLED', raising=False)
    with patch.object(pdf, 'make_pdf') as render:
        pdf.send_pdf_report('daily', [], ['report'])
        render.assert_not_called()


@pytest.mark.parametrize('status,ok', [(400, False), (200, False)])
def test_pdf_delivery_failure_is_not_silently_success(monkeypatch, status, ok):
    monkeypatch.setenv('PDF_REPORT_ENABLED', 'true')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'unit-test-not-a-real-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'unit-test-chat')
    def fake_pdf(body, target):
        target.write_bytes(b'%PDF-test-only')
    response = Mock(ok=status == 200, status_code=status)
    response.json.return_value = {'ok': ok}
    with patch.object(pdf, 'make_pdf', side_effect=fake_pdf), patch.object(pdf.requests, 'post', return_value=response):
        with pytest.raises(RuntimeError):
            pdf.send_pdf_report('daily', [], ['report'])


def test_pdf_delivery_uses_original_body_and_attachment(monkeypatch, capsys):
    monkeypatch.setenv('PDF_REPORT_ENABLED', 'true')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'unit-test-not-a-real-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'unit-test-chat')
    response = Mock(ok=True)
    response.json.return_value = {'ok': True, 'result': {'message_id': 123, 'document': {'file_size': 14}}}
    def fake_pdf(body, target):
        assert '原版文字全文' in body
        target.write_bytes(b'%PDF-test-only')
    with patch.object(pdf, 'make_pdf', side_effect=fake_pdf), patch.object(pdf.requests, 'post', return_value=response) as post:
        pdf.send_pdf_report('daily', [row()], ['原版文字全文'])
        assert post.call_args.kwargs['files']['document'][2] == 'application/pdf'
        assert post.call_args.kwargs['data']['chat_id'] == 'unit-test-chat'
    assert 'PDF_DELIVERED' in capsys.readouterr().out


def test_sheet_premarket_keeps_stock_id_as_text():
    sheet = Mock()
    sheet.worksheet.return_value.get_all_records.return_value = []
    with patch('stock_strategies.sheet.get_gsheet', return_value=sheet):
        assert read_latest_signals() == []
    sheet.worksheet.return_value.get_all_records.assert_called_once_with(numericise_ignore=[2])
