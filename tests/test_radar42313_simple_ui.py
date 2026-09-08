"""Public Radar UI regression: simple navigation, unchanged 48h retention."""
import ast
import asyncio
import html
import re
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'bot.py').read_text(encoding='utf-8')


def load_functions(*names, **overrides):
    tree = ast.parse(SOURCE)
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    ns = dict(InlineKeyboardButton=lambda **kwargs: SimpleNamespace(**kwargs),
              InlineKeyboardMarkup=lambda **kwargs: SimpleNamespace(**kwargs),
              allowed=lambda user_id: user_id == 1, FREE_RADAR_PREVIEW_LIMIT=5,
              html=html, re=re, RADAR_PAGE_SIZE=12, CATEGORIES={}, GROUPS={},
              radar_price_keyboard=lambda *args, **kwargs: None,
              price_filter_label=lambda value: str(value))
    ns.update(overrides)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT/'bot.py'), 'exec'), ns)
    return ns


def test_original_navigation_without_extra_historical_tab():
    ns = load_functions('radar_best_keyboard')
    for user_id in (1, 2):
        rows = ns['radar_best_keyboard'](user_id).inline_keyboard
        labels = [button.text for row in rows for button in row]
        callbacks = [button.callback_data for row in rows for button in row]
        assert not any('48' in label for label in labels)
        assert not any('hot48' in value for value in callbacks)
        assert 'radarlist:hot:0' in callbacks
        assert 'radarlist:rising:0' in callbacks
        if user_id == 1:
            assert 'radarlist:alltime:0' in callbacks
        else:
            assert 'radar_locked:records' in callbacks


def test_home_has_no_implementation_window_or_extra_counter():
    async def stats():
        return SimpleNamespace(total=90, hot=17, rising=44, fast_sold=0, recent_hot_48h=61)
    ns = load_functions('_radar_home_text', radar_stats=stats)
    for user_id in (1, 2):
        text = asyncio.run(ns['_radar_home_text'](user_id))
        assert '48ч' not in text and '48 часов' not in text
        assert 'Сильные за' not in text
        assert '90' in text and '17' in text and '44' in text
        assert 'Observed Score' in text


def test_old_hot48_links_open_existing_history_not_dead_feed():
    seen = []
    async def list_products(**kwargs):
        seen.append(kwargs)
        return [], 0
    ns = load_functions('_radar_context_back', '_radar_list_payload', 'radar_list_keyboard',
                        list_radar_products=list_products)
    assert ns['_radar_context_back']({'radar_context_kind':'list', 'radar_context_mode':'hot48', 'radar_context_page':2})[0] == 'radarlist:alltime:2'
    text, markup = asyncio.run(ns['_radar_list_payload'](1, 'hot48', 0))
    assert seen[0]['mode'] == 'alltime'
    assert 'Рекорды Radar' in text
    assert 'Сильные за 48' not in text
    assert any(button.callback_data == 'radarbest' for row in markup.inline_keyboard for button in row)


def test_retention_and_scoring_modules_are_untouched():
    radar = (ROOT/'radar.py').read_text(encoding='utf-8')
    assert 'RADAR_V3_LIVE_RETENTION_HOURS = 48' in radar
    assert 'RADAR_V3_CURRENT_SIGNAL_HOURS = 6' in radar
    assert 'RADAR_V3_MAX_OBSERVATION_HOURS = 6' in radar
    assert 'RADAR_V3_CANDIDATE_PERCENTILE = 0.90' in radar
    assert 'RADAR_V3_STRONG_PERCENTILE = 0.98' in radar
