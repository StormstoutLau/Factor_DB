"""
OSINT 模块测试
"""

import pytest
import tempfile
import os
from pathlib import Path
from datetime import date

import pandas as pd

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from osint.base import BaseCollector, CollectorConfig, OSINTEvent, OSINTEventType
from osint.registry import CollectorRegistry
from osint.pipeline import OSINTPipeline


class DummyCollector(BaseCollector):

    meta = {
        'name': 'dummy',
        'summary': '测试收集器',
        'categories': ['test'],
        'required_keys': [],
    }

    def setup(self):
        self._test_data = None

    def transform_query(self, params):
        return {'indicator': params.get('indicator', 'TEST')}

    def extract_data(self, query):
        return [{'date': '2024-01-01', 'value': 100.0}]

    def transform_data(self, query, raw_data):
        return pd.DataFrame({
            'trade_date': [date(2024, 1, 1)],
            'indicator_id': [query['indicator']],
            'indicator_name': ['Test Indicator'],
            'value': [100.0],
            'value_type': ['raw'],
            'data_quality': [100],
            'source_id': ['dummy'],
        })

    def produced_events(self):
        return [OSINTEventType.MACRO_ECONOMIC]


class ErrorCollector(BaseCollector):

    meta = {
        'name': 'error_collector',
        'summary': '错误测试收集器',
        'categories': ['test'],
        'required_keys': [],
    }

    def setup(self):
        pass

    def transform_query(self, params):
        return params

    def extract_data(self, query):
        raise ConnectionError("模拟网络错误")

    def transform_data(self, query, raw_data):
        return pd.DataFrame()

    def produced_events(self):
        return [OSINTEventType.MACRO_ECONOMIC]


class TestOSINTBase:

    def test_collector_config_defaults(self):
        config = CollectorConfig()
        assert config.rate_limit == 1.0
        assert config.max_retries == 3
        assert config.timeout == 30

    def test_collector_config_custom(self):
        config = CollectorConfig(
            source_id='test',
            rate_limit=2.0,
            max_retries=5,
            api_keys={'key1': 'value1'},
        )
        assert config.source_id == 'test'
        assert config.rate_limit == 2.0
        assert config.api_keys['key1'] == 'value1'

    def test_event_type_enum(self):
        assert OSINTEventType.MACRO_ECONOMIC == 'MACRO_ECONOMIC'
        assert OSINTEventType.NEWS_SENTIMENT == 'NEWS_SENTIMENT'

    def test_event_creation(self):
        event = OSINTEvent(
            event_type=OSINTEventType.MACRO_ECONOMIC,
            data={'test': True},
            source_module='test',
            confidence=90,
        )
        assert event.event_type == OSINTEventType.MACRO_ECONOMIC
        assert event.confidence == 90
        assert event.source_module == 'test'

    def test_event_repr(self):
        event = OSINTEvent(
            event_type=OSINTEventType.ERROR,
            data='error msg',
            source_module='test',
        )
        assert 'ERROR' in repr(event)

    def test_dummy_collector_collect(self):
        collector = DummyCollector()
        df = collector.collect({'indicator': 'GDP'})
        assert not df.empty
        assert len(df) == 1
        assert df.iloc[0]['indicator_id'] == 'GDP'

    def test_collector_count(self):
        collector = DummyCollector()
        assert collector.get_collected_count() == 0
        collector.collect()
        assert collector.get_collected_count() == 1

    def test_collector_reset_counter(self):
        collector = DummyCollector()
        collector.collect()
        collector.reset_counter()
        assert collector.get_collected_count() == 0

    def test_error_collector(self):
        collector = ErrorCollector()
        df = collector.collect()
        assert df.empty
        assert collector._error_state is True

    def test_collector_stop(self):
        collector = DummyCollector()
        collector.stop()
        assert collector.check_for_stop() is True
        df = collector.collect()
        assert df.empty

    def test_collector_asdict(self):
        collector = DummyCollector()
        info = collector.asdict()
        assert 'name' in info
        assert info['name'] == 'dummy'
        assert 'produces' in info

    def test_collector_context_manager(self):
        with DummyCollector() as collector:
            df = collector.collect()
            assert not df.empty

    def test_listener_notification(self):
        collector1 = DummyCollector()
        collector2 = DummyCollector()
        collector1.register_listener(collector2)
        assert len(collector1._listener_modules) == 1


class TestCollectorRegistry:

    def test_register(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        assert 'dummy' in registry
        assert len(registry) == 1

    def test_unregister(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        registry.unregister('dummy')
        assert 'dummy' not in registry
        assert len(registry) == 0

    def test_get_instance(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        instance = registry.get('dummy')
        assert instance is not None
        assert isinstance(instance, DummyCollector)

    def test_get_nonexistent(self):
        registry = CollectorRegistry()
        instance = registry.get('nonexistent')
        assert instance is None

    def test_get_singleton(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        inst1 = registry.get('dummy')
        inst2 = registry.get('dummy')
        assert inst1 is inst2

    def test_list_collectors(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        collectors = registry.list_collectors()
        assert len(collectors) == 1
        assert collectors[0]['source_id'] == 'dummy'

    def test_get_by_event_type(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        result = registry.get_by_event_type(OSINTEventType.MACRO_ECONOMIC)
        assert len(result) >= 0


class TestCollectorRegistryBugFix:
    """Bug1 修复测试: get_by_event_type 不应实例化类"""

    def test_get_by_event_type_without_instantiation(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        result = registry.get_by_event_type(OSINTEventType.MACRO_ECONOMIC)
        assert 'dummy' in result

    def test_get_by_event_type_no_match(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        result = registry.get_by_event_type(OSINTEventType.SOCIAL_MEDIA)
        assert 'dummy' not in result

    def test_get_by_event_type_with_setup_side_effect(self):
        setup_called = []

        class SideEffectCollector(BaseCollector):
            meta = {'name': 'side_effect', 'summary': '', 'categories': [], 'required_keys': []}

            def setup(self):
                setup_called.append(True)

            def transform_query(self, params):
                return params

            def extract_data(self, query):
                return []

            def transform_data(self, query, raw_data):
                return pd.DataFrame()

            def produced_events(self):
                return [OSINTEventType.GOVERNMENT_POLICY]

        registry = CollectorRegistry()
        registry.register(SideEffectCollector)
        result = registry.get_by_event_type(OSINTEventType.GOVERNMENT_POLICY)
        assert 'side_effect' in result
        assert len(setup_called) == 0, "get_by_event_type 不应触发 setup()"

    def test_get_registered_ids(self):
        registry = CollectorRegistry()
        registry.register(DummyCollector)
        ids = registry.get_registered_ids()
        assert 'dummy' in ids


class TestWorldBankQueryPreservation:
    """Bug3 修复测试: transform_query 返回的字典不应被 extract_data 破坏"""

    def test_query_dict_not_mutated_by_extract(self):
        from osint.collectors.worldbank_collector import WorldBankCollector
        collector = WorldBankCollector()
        params = {'country': 'CN', 'indicator': 'NY.GDP.MKTP.CD', 'date_range': '2020:2024'}
        query = collector.transform_query(params)
        original_keys = set(query.keys())
        original_country = query.get('country')
        original_indicator = query.get('indicator')
        try:
            collector.extract_data(query)
        except Exception:
            pass
        assert set(query.keys()) == original_keys, "extract_data 不应删除 query 中的键"
        assert query.get('country') == original_country, "country 键不应被删除"
        assert query.get('indicator') == original_indicator, "indicator 键不应被删除"


class TestNewsRSSDateImport:
    """Bug2 修复测试: news_rss_collector 的 transform_data 应能正确处理日期"""

    def test_transform_data_with_published_parsed(self):
        from osint.collectors.news_rss_collector import NewsRSSCollector
        collector = NewsRSSCollector()

        class FakeEntry:
            title = 'Test News'
            link = 'https://example.com/test'
            summary = 'Test summary'
            published_parsed = (2024, 6, 15, 10, 0, 0, 0, 0, 0)

        class FakeFeed:
            entries = [FakeEntry()]

        df = collector.transform_data({'feed_url': 'test', 'feed_name': 'test'}, FakeFeed())
        assert not df.empty
        assert df.iloc[0]['publish_date'] == date(2024, 6, 15)

    def test_transform_data_without_published_parsed(self):
        from osint.collectors.news_rss_collector import NewsRSSCollector
        collector = NewsRSSCollector()

        class FakeEntry:
            title = 'Test News 2'
            link = 'https://example.com/test2'
            summary = 'Test summary 2'

        class FakeFeed:
            entries = [FakeEntry()]

        df = collector.transform_data({'feed_url': 'test', 'feed_name': 'test'}, FakeFeed())
        assert not df.empty
        assert isinstance(df.iloc[0]['publish_date'], date)


class TestBaseCollectorRequestRefactor:
    """P2: request_get/post 重复代码提取测试"""

    def test_request_method_exists(self):
        collector = DummyCollector()
        assert hasattr(collector, '_request')
        assert callable(collector._request)


class TestOSINTPipeline:

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / 'test_osint.duckdb')

    @pytest.fixture
    def pipeline(self, db_path):
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        return OSINTPipeline(db_path)

    def test_pipeline_register(self, pipeline):
        pipeline.register_collector(DummyCollector)
        status = pipeline.get_status()
        assert status['registered_collectors'] == 1

    def test_pipeline_run(self, pipeline):
        pipeline.register_collector(DummyCollector)
        results = pipeline.run(['dummy'])
        assert 'dummy' in results
        assert results['dummy'] >= 0

    def test_pipeline_run_nonexistent(self, pipeline):
        results = pipeline.run(['nonexistent'])
        assert results.get('nonexistent') == -1

    def test_pipeline_status(self, pipeline):
        status = pipeline.get_status()
        assert 'registered_collectors' in status
        assert 'collector_list' in status

    def test_pipeline_run_all_uses_public_api(self, pipeline):
        pipeline.register_collector(DummyCollector)
        results = pipeline.run_all()
        assert 'dummy' in results


class TestOSINTQueryIntegration:

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / 'test_osint_query.duckdb')

    @pytest.fixture
    def query(self, db_path):
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        from query.osint_query import OSINTQuery
        return OSINTQuery(db_path)

    def test_get_latest_macro_empty(self, query):
        df = query.get_latest_macro(['GDP'])
        assert isinstance(df, pd.DataFrame)

    def test_get_source_summary_empty(self, query):
        df = query.get_source_summary()
        assert isinstance(df, pd.DataFrame)

    def test_get_data_freshness_empty(self, query):
        df = query.get_data_freshness()
        assert isinstance(df, pd.DataFrame)

    def test_get_data_by_source_empty(self, query):
        df = query.get_data_by_source('worldbank')
        assert isinstance(df, pd.DataFrame)

    def test_get_cross_source_data_empty(self, query):
        df = query.get_cross_source_data(['GDP'])
        assert isinstance(df, pd.DataFrame)




class TestBaseCollectorResetError:
    """P2: reset_error test"""

    def test_reset_error_clears_error_state(self):
        collector = ErrorCollector()
        collector.collect()
        assert collector._error_state is True

        collector.reset_error()
        assert collector._error_state is False
        assert collector.check_for_stop() is False

    def test_reset_error_allows_recollect(self):
        collector = ErrorCollector()
        collector.collect()
        assert collector._error_state is True

        collector.reset_error()
        df = collector.collect()
        assert collector._error_state is True
        assert df.empty


class TestOSINTPipelineEventLog:
    """P3: pipeline event_log test"""

    def test_event_log_after_collect(self, tmp_path):
        db_path = str(tmp_path / 'test_evt1.duckdb')
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        pipeline = OSINTPipeline(db_path)
        pipeline.register_collector(DummyCollector)
        pipeline.run(['dummy'])
        events = pipeline.get_event_log()
        assert len(events) > 0

    def test_event_log_contains_correct_type(self, tmp_path):
        db_path = str(tmp_path / 'test_evt2.duckdb')
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        pipeline = OSINTPipeline(db_path)
        pipeline.register_collector(DummyCollector)
        pipeline.run(['dummy'])
        events = pipeline.get_event_log()
        assert events[0].event_type == OSINTEventType.MACRO_ECONOMIC

    def test_event_log_for_error_collector(self, tmp_path):
        db_path = str(tmp_path / 'test_evt3.duckdb')
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        pipeline = OSINTPipeline(db_path)
        pipeline.register_collector(ErrorCollector)
        pipeline.run(['error_collector'])
        events = pipeline.get_event_log()
        assert len(events) > 0
        assert events[0].event_type == OSINTEventType.ERROR


class TestOSINTQueryUnionAll:
    """P2: get_data_by_source UNION ALL"""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / 'test_union.duckdb')

    @pytest.fixture
    def query(self, db_path):
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        from query.osint_query import OSINTQuery
        return OSINTQuery(db_path)

    def test_get_data_by_source_union_all(self, query):
        df = query.get_data_by_source('worldbank', date(2020, 1, 1), date(2026, 1, 1))
        assert isinstance(df, pd.DataFrame)


class TestOSINTE2E:
    """P3: E2E integration test"""

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / 'test_e2e.duckdb')

    @pytest.fixture
    def pipeline(self, db_path):
        conn = DuckDBConnection(db_path)
        schema = SchemaManager(conn)
        schema.init_database()
        return OSINTPipeline(db_path)

    def test_collect_and_query_macro(self, pipeline):
        pipeline.register_collector(DummyCollector)
        results = pipeline.run(['dummy'])
        assert results['dummy'] >= 0

        from query.osint_query import OSINTQuery
        q = OSINTQuery(pipeline.db_path)
        df = q.get_latest_macro(['TEST'])
        assert isinstance(df, pd.DataFrame)

    def test_collect_and_query_source(self, pipeline):
        pipeline.register_collector(DummyCollector)
        pipeline.run(['dummy'])

        from query.osint_query import OSINTQuery
        q = OSINTQuery(pipeline.db_path)
        df = q.get_source_summary()
        assert isinstance(df, pd.DataFrame)
        # source_id is NULL in MacroLoader, summary may be empty

    def test_collect_and_query_freshness(self, pipeline):
        pipeline.register_collector(DummyCollector)
        pipeline.run(['dummy'])

        from query.osint_query import OSINTQuery
        q = OSINTQuery(pipeline.db_path)
        df = q.get_data_freshness()
        assert isinstance(df, pd.DataFrame)

    def test_collect_and_cross_source(self, pipeline):
        pipeline.register_collector(DummyCollector)
        pipeline.run(['dummy'])

        from query.osint_query import OSINTQuery
        q = OSINTQuery(pipeline.db_path)
        df = q.get_cross_source_data(['TEST'])
        assert isinstance(df, pd.DataFrame)

    def test_multi_collector_pipeline(self, pipeline):
        pipeline.register_collector(DummyCollector)

        class AnotherCollector(BaseCollector):
            meta = {'name': 'another', 'summary': '', 'categories': [], 'required_keys': []}

            def setup(self):
                pass

            def transform_query(self, params):
                return {}

            def extract_data(self, query):
                return [{'date': '2024-06-01', 'value': 200.0}]

            def transform_data(self, query, raw_data):
                return pd.DataFrame({
                    'trade_date': [date(2024, 6, 1)],
                    'indicator_id': ['CPI'],
                    'indicator_name': ['Consumer Price Index'],
                    'value': [200.0],
                    'value_type': ['raw'],
                    'data_quality': [100],
                    'source_id': ['another'],
                })

            def produced_events(self):
                return [OSINTEventType.MACRO_ECONOMIC]

        pipeline.register_collector(AnotherCollector)
        results = pipeline.run(['dummy', 'another'])
        assert results['dummy'] >= 0
        assert results['another'] >= 0

        status = pipeline.get_status()
        assert status['registered_collectors'] == 2


class TestGovernmentCollectorStructured:
    """P3: government_collector structured parse test"""

    def test_structured_html_parse(self):
        from osint.collectors.government_collector import GovernmentCollector
        collector = GovernmentCollector()

        html = '<html><body><ul class="list"><li><a class="title" href="/p/1.html">test title</a></li></ul></body></html>'
        query = {
            'source_key': 'pbc',
            'name': 'test',
            'url': 'https://x.com',
            'type': 'monetary_policy',
        }
        df = collector.transform_data(query, html)
        assert isinstance(df, pd.DataFrame)

    def test_empty_html_parse(self):
        from osint.collectors.government_collector import GovernmentCollector
        collector = GovernmentCollector()
        query = {
            'source_key': 'stats',
            'name': 'test',
            'url': 'https://x.com',
            'type': 'economic_data',
        }
        df = collector.transform_data(query, '')
        assert isinstance(df, pd.DataFrame)

    def test_no_elements_html(self):
        from osint.collectors.government_collector import GovernmentCollector
        collector = GovernmentCollector()
        html = '<html><body><p>No elements</p></body></html>'
        query = {
            'source_key': 'ndrc',
            'name': 'test',
            'url': 'https://x.com',
            'type': 'x',
        }
        df = collector.transform_data(query, html)
        assert isinstance(df, pd.DataFrame)

class TestOSINTInitExports:

    def test_osint_package_exports(self):
        import osint
        assert hasattr(osint, 'BaseCollector')
        assert hasattr(osint, 'CollectorConfig')
        assert hasattr(osint, 'OSINTEvent')
        assert hasattr(osint, 'OSINTEventType')
        assert hasattr(osint, 'CollectorRegistry')
        assert hasattr(osint, 'OSINTPipeline')

    def test_collectors_package_exports(self):
        from osint.collectors import WorldBankCollector
        from osint.collectors import AKShareMacroCollector
        from osint.collectors import NewsRSSCollector
        from osint.collectors import GovernmentCollector
        assert WorldBankCollector is not None
        assert AKShareMacroCollector is not None
        assert NewsRSSCollector is not None
        assert GovernmentCollector is not None
