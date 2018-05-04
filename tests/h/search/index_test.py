# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import mock
import pytest

from h import presenters
from h import search
from h.search import index as search_index


class TestIndexAnnotationDocuments(object):
    """
    Integrated tests for indexing and retrieving annotations documents.

    Tests that index real annotations into a real Elasticsearch index, and
    test the saving and retrieval of the ``annotation.document`` field.

    *Searching* for an annotation by ``annotation.document`` (e.g. by document
    ``title`` or ``web_uri``) isn't enabled.  But you can retrieve an
    annotation by ID, or by searching on other field(s), and then access
    its ``document``. Bouncer (https://github.com/hypothesis/bouncer) accesses
    h's Elasticsearch index directly and uses this ``document`` field.

    """
    def test_it_can_index_an_annotation_with_no_document(self, factories,
                                                         index, get):
        annotation = factories.Annotation.build(id="test_annotation_id",
                                                document=None)

        index(annotation)

        assert get(annotation.id)["document"] == {}

    def test_it_indexes_the_annotations_document_title(self, factories,
                                                       index, get):
        annotation = factories.Annotation.build(
            id="test_annotation_id",
            document=factories.Document.build(title="test_document_title"),
        )

        index(annotation)

        assert get(annotation.id)["document"]["title"] == ["test_document_title"]

    def test_it_can_index_an_annotation_with_a_document_with_no_title(self, factories,
                                                                      index, get):
        annotation = factories.Annotation.build(
            id="test_annotation_id",
            document=factories.Document.build(title=None),
        )

        index(annotation)

        assert "title" not in get(annotation.id)["document"]

    @pytest.fixture
    def get(self, es_client):
        def _get(annotation_id):
            """Return the annotation with the given ID from Elasticsearch."""
            return es_client.conn.get(
                index=es_client.index, doc_type="annotation",
                id=annotation_id)["_source"]
        return _get


class TestIndex(object):
    """Elasticsearch-integrated tests for :py:func:`h.search.index.index()`."""

    def test_you_can_search_for_an_annotation_by_authority(self, es_client, factories, index):
        annotation = factories.Annotation.build(
            id="test_annotation_id",
            userid="acct:someone@example.com",
        )

        index(annotation)

        es_client.conn.indices.refresh(index=es_client.index)
        search_results = [hit["_id"] for hit in es_client.conn.search(
            index=es_client.index,
            doc_type="annotation",
            _source=False,
            body={
                "query": {
                    "filtered": {
                        "filter": {
                            "term": {
                                "authority": "example.com"
                            }
                        }
                    }
                }
            }
        )["hits"]["hits"]]

        assert search_results == [annotation.id]


@pytest.mark.usefixtures('presenters')
class TestIndexAnnotation:

    def test_it_presents_the_annotation(self, es, presenters, pyramid_request):
        annotation = mock.Mock()

        search_index.index(es, annotation, pyramid_request)

        presenters.AnnotationSearchIndexPresenter.assert_called_once_with(annotation)

    def test_it_creates_an_annotation_before_save_event(self,
                                                        AnnotationTransformEvent,
                                                        es,
                                                        presenters,
                                                        pyramid_request):
        annotation = mock.Mock()
        presented = presenters.AnnotationSearchIndexPresenter.return_value.asdict()

        search_index.index(es, annotation, pyramid_request)

        AnnotationTransformEvent.assert_called_once_with(pyramid_request, annotation, presented)

    def test_it_notifies_before_save_event(self,
                                           AnnotationTransformEvent,
                                           es,
                                           notify,
                                           presenters,
                                           pyramid_request):
        search_index.index(es, mock.Mock(), pyramid_request)

        event = AnnotationTransformEvent.return_value
        notify.assert_called_once_with(event)

    def test_it_indexes_the_annotation(self, es, presenters, pyramid_request):
        search_index.index(es, mock.Mock(), pyramid_request)

        es.conn.index.assert_called_once_with(
            index='hypothesis',
            doc_type='annotation',
            body=presenters.AnnotationSearchIndexPresenter.return_value.asdict.return_value,
            id='test_annotation_id',
        )

    def test_it_allows_to_override_target_index(self, es, presenters, pyramid_request):
        search_index.index(es, mock.Mock(), pyramid_request, target_index='custom-index')

        _, kwargs = es.conn.index.call_args
        assert kwargs['index'] == 'custom-index'

    @pytest.fixture
    def presenters(self, patch):
        presenters = patch('h.search.index.presenters')
        presenter = presenters.AnnotationSearchIndexPresenter.return_value
        presenter.asdict.return_value = {
            'id': 'test_annotation_id',
            'target': [
                {
                    'source': 'http://example.com/example',
                },
            ],
        }
        return presenters


class TestDeleteAnnotation:

    def test_it_marks_annotation_as_deleted(self, es):
        search.index.delete(es, 'test_annotation_id')

        es.conn.index.assert_called_once_with(
            index='hypothesis',
            doc_type='annotation',
            body={'deleted': True},
            id='test_annotation_id'
        )

    def test_it_allows_to_override_target_index(self, es):
        search.index.delete(es, 'test_annotation_id', target_index='custom-index')

        _, kwargs = es.conn.index.call_args
        assert kwargs['index'] == 'custom-index'


class TestBatchIndexer(object):
    def test_index_indexes_all_annotations_to_es(self, db_session, indexer, matchers, streaming_bulk, factories):
        ann_1, ann_2 = factories.Annotation(), factories.Annotation()

        indexer.index()

        streaming_bulk.assert_called_once_with(
            indexer.es_client.conn, matchers.iterable_with(matchers.unordered_list([ann_1, ann_2])),
            chunk_size=mock.ANY, raise_on_error=False, expand_action_callback=mock.ANY)

    def test_index_skips_deleted_annotations_when_indexing_all(self, db_session, indexer, matchers, streaming_bulk, factories):
        ann_1, ann_2 = factories.Annotation(), factories.Annotation()
        # create deleted annotations
        factories.Annotation(deleted=True)
        factories.Annotation(deleted=True)

        indexer.index()

        streaming_bulk.assert_called_once_with(
            indexer.es_client.conn, matchers.iterable_with(matchers.unordered_list([ann_1, ann_2])),
            chunk_size=mock.ANY, raise_on_error=False, expand_action_callback=mock.ANY)

    def test_index_indexes_filtered_annotations_to_es(self, db_session, indexer, matchers, streaming_bulk, factories):
        _, ann_2 = factories.Annotation(), factories.Annotation()  # noqa: F841

        indexer.index([ann_2.id])

        streaming_bulk.assert_called_once_with(
            indexer.es_client.conn, matchers.iterable_with([ann_2]),
            chunk_size=mock.ANY, raise_on_error=False, expand_action_callback=mock.ANY)

    def test_index_skips_deleted_annotations_when_indexing_filtered(self, db_session, indexer, matchers, streaming_bulk, factories):
        factories.Annotation()
        ann_2 = factories.Annotation()
        # create deleted annotations
        factories.Annotation(deleted=True)
        factories.Annotation(deleted=True)

        indexer.index([ann_2.id])

        streaming_bulk.assert_called_once_with(
            indexer.es_client.conn, matchers.iterable_with([ann_2]),
            chunk_size=mock.ANY, raise_on_error=False, expand_action_callback=mock.ANY)

    def test_index_correctly_presents_bulk_actions(self,
                                                   db_session,
                                                   indexer,
                                                   pyramid_request,
                                                   streaming_bulk,
                                                   factories):
        annotation = factories.Annotation()
        db_session.add(annotation)
        db_session.flush()
        results = []

        def fake_streaming_bulk(*args, **kwargs):
            ann = list(args[1])[0]
            callback = kwargs.get('expand_action_callback')
            results.append(callback(ann))
            return set()

        streaming_bulk.side_effect = fake_streaming_bulk

        indexer.index()

        rendered = presenters.AnnotationSearchIndexPresenter(annotation).asdict()
        rendered['target'][0]['scope'] = [annotation.target_uri_normalized]
        assert results[0] == (
            {'index': {'_type': indexer.es_client.t.annotation,
                       '_index': 'hypothesis',
                       '_id': annotation.id}},
            rendered
        )

    def test_index_allows_to_set_op_type(self, db_session, es, pyramid_request, streaming_bulk, factories):
        indexer = search.index.BatchIndexer(db_session, es, pyramid_request,
                                            op_type='create')
        annotation = factories.Annotation()
        db_session.add(annotation)
        db_session.flush()
        results = []

        def fake_streaming_bulk(*args, **kwargs):
            ann = list(args[1])[0]
            callback = kwargs.get('expand_action_callback')
            results.append(callback(ann))
            return set()

        streaming_bulk.side_effect = fake_streaming_bulk

        indexer.index()

        rendered = presenters.AnnotationSearchIndexPresenter(annotation).asdict()
        rendered['target'][0]['scope'] = [annotation.target_uri_normalized]
        assert results[0] == (
            {'create': {'_type': indexer.es_client.t.annotation,
                        '_index': 'hypothesis',
                        '_id': annotation.id}},
            rendered
        )

    def test_index_emits_AnnotationTransformEvent_when_presenting_bulk_actions(self,
                                                                               db_session,
                                                                               indexer,
                                                                               pyramid_request,
                                                                               streaming_bulk,
                                                                               pyramid_config,
                                                                               factories):

        annotation = factories.Annotation()
        results = []

        def fake_streaming_bulk(*args, **kwargs):
            ann = list(args[1])[0]
            callback = kwargs.get('expand_action_callback')
            results.append(callback(ann))
            return set()

        streaming_bulk.side_effect = fake_streaming_bulk

        def transform(event):
            if event.annotation == annotation:
                data = event.annotation_dict
                data['transformed'] = True

        pyramid_config.add_subscriber(transform, 'h.events.AnnotationTransformEvent')

        indexer.index()

        rendered = presenters.AnnotationSearchIndexPresenter(annotation).asdict()
        rendered['transformed'] = True
        rendered['target'][0]['scope'] = [annotation.target_uri_normalized]

        assert results[0] == (
            {'index': {'_type': indexer.es_client.t.annotation,
                       '_index': 'hypothesis',
                       '_id': annotation.id}},
            rendered
        )

    def test_index_returns_failed_bulk_actions_for_default_op_type(self, db_session, indexer, streaming_bulk, factories):
        ann_success_1, ann_success_2 = factories.Annotation(), factories.Annotation()
        ann_fail_1, ann_fail_2 = factories.Annotation(), factories.Annotation()

        def fake_streaming_bulk(*args, **kwargs):
            for ann in args[1]:
                if ann.id in [ann_fail_1.id, ann_fail_2.id]:
                    yield (False, {'index': {'_id': ann.id, 'error': 'unknown error'}})
                elif ann.id in [ann_success_1.id, ann_success_2.id]:
                    yield (True, {'index': {'_id': ann.id}})

        streaming_bulk.side_effect = fake_streaming_bulk

        result = indexer.index()
        assert result == set([ann_fail_1.id, ann_fail_2.id])

    def test_index_returns_failed_bulk_actions_for_create_op_type(self, pyramid_request, es, db_session, streaming_bulk, factories):
        indexer = search.index.BatchIndexer(db_session, es, pyramid_request,
                                            op_type='create')

        ann_success_1, ann_success_2 = factories.Annotation(), factories.Annotation()
        ann_fail_1, ann_fail_2 = factories.Annotation(), factories.Annotation()

        def fake_streaming_bulk(*args, **kwargs):
            for ann in args[1]:
                if ann.id in [ann_fail_1.id, ann_fail_2.id]:
                    yield (False, {'create': {'_id': ann.id, 'error': 'unknown error'}})
                elif ann.id in [ann_success_1.id, ann_success_2.id]:
                    yield (True, {'create': {'_id': ann.id}})

        streaming_bulk.side_effect = fake_streaming_bulk

        result = indexer.index()
        assert result == set([ann_fail_1.id, ann_fail_2.id])

    def test_index_ignores_document_exists_errors_for_op_type_create(self, db_session, es, pyramid_request, streaming_bulk, factories):
        indexer = search.index.BatchIndexer(db_session, es, pyramid_request,
                                            op_type='create')

        ann_success_1, ann_success_2 = factories.Annotation(), factories.Annotation()
        ann_fail_1, ann_fail_2 = factories.Annotation(), factories.Annotation()

        def fake_streaming_bulk(*args, **kwargs):
            for ann in args[1]:
                if ann.id in [ann_fail_1.id, ann_fail_2.id]:
                    error = 'DocumentAlreadyExistsException[[index-name][1] [annotation][gibberish]: ' \
                            'document already exists]'
                    yield (False, {'create': {'_id': ann.id, 'error': error}})
                elif ann.id in [ann_success_1.id, ann_success_2.id]:
                    yield (True, {'create': {'_id': ann.id}})

        streaming_bulk.side_effect = fake_streaming_bulk

        result = indexer.index()
        assert len(result) == 0

    @pytest.fixture
    def indexer(self, db_session, es, pyramid_request):
        return search.index.BatchIndexer(db_session, es, pyramid_request)

    @pytest.fixture
    def index(self, patch):
        return patch('h.search.index.BatchIndexer.index')

    @pytest.fixture
    def streaming_bulk(self, patch):
        return patch('h.search.index.es_helpers.streaming_bulk')


@pytest.fixture
def es():
    mock_es = mock.create_autospec(search.client.Client, instance=True, spec_set=True,
                                   index="hypothesis")
    mock_es.t.annotation = 'annotation'
    return mock_es


@pytest.fixture
def AnnotationTransformEvent(patch):
    return patch('h.search.index.AnnotationTransformEvent')


@pytest.fixture
def index(es_client, pyramid_request):
    def _index(annotation):
        """Index the given annotation into Elasticsearch."""
        search_index.index(es_client, annotation, pyramid_request)
    return _index
