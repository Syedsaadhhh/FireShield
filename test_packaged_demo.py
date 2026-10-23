"""Encoded source identity and signed handoff regression checks."""
import hashlib
import json
from pathlib import Path

import pytest

import pipeline_service as domain
from packaged_demo import prepare_package, packaging_for_asset, export_native_release, native_bundle_bytes
from pipeline_service import LocalPipelineService, PipelineServiceError
from screening_analyzer import find_ffmpeg_executable


@pytest.fixture
def service(tmp_path):
    return LocalPipelineService(store_path=tmp_path / 'store.json')


def test_benign_decoder_never_substitutes_reference_frames(service, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('decoder unavailable')
    monkeypatch.setattr(domain, 'analyze_video_file_headless', unavailable)
    job = service.start_screening_job(tenant_id='publisher-demo-tenant', asset_id='ast-benign-navy-clip')
    assert job.status == 'FAILED'
    with pytest.raises(PipelineServiceError):
        service.get_screening_report(job.asset_revision_id, 'publisher-demo-tenant')


@pytest.mark.skipif(not find_ffmpeg_executable(), reason='FFmpeg required for actual encoded-media test')
def test_encoded_media_review_and_export_keep_one_identity(service, tmp_path):
    tenant = 'publisher-demo-tenant'
    asset = prepare_package(service, tenant)
    source = domain.WORKSPACE_ROOT / asset.source_path
    assert asset.asset_revision_id == 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()
    job = service.start_screening_job(tenant_id=tenant, asset_id=asset.asset_id)
    assert job.status == 'COMPLETED'
    report = service.get_screening_report(asset.asset_revision_id, tenant)
    assert report['total_frames'] == 900
    assert report['asset_revision_id'] == asset.asset_revision_id
    assert report['findings']
    packaging = packaging_for_asset(asset)
    kwargs = dict(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
                  rendition_set_id='owned-hls', playlist_manifest=packaging['playlist_manifest'],
                  media_uri=packaging['media_uri'])
    with pytest.raises(PipelineServiceError):
        service.publish_release(**kwargs)
    for finding in report['findings']:
        note = service.ground_context_for_interval(tenant_id=tenant,
            asset_revision_id=asset.asset_revision_id, interval_id=finding['interval_id'])
        assert note['model_invoked'] is False
        service.approve_context_note(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
            interval_id=finding['interval_id'], reviewer_id='controlled-test-editor')
    service.submit_editorial_decision(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
        reviewer_id='controlled-test-editor', decision='approved', notes='Controlled encoded-media test')
    release = service.publish_release(**kwargs)
    exported = export_native_release(release, asset, tmp_path / 'native')
    assert exported['asset_revision_id'] == asset.asset_revision_id
    assert json.loads(exported['sidecar_json'])['asset_revision_id'] == asset.asset_revision_id
    assert (tmp_path / 'native/media.m3u8').read_text() == release.playlist_manifest
    import io, zipfile
    bundle = native_bundle_bytes(release, asset)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert set(archive.namelist()) == {'content.ts', 'media.m3u8', 'context.vtt', 'native-release.json', 'README.txt'}
        public_release = json.loads(archive.read('native-release.json'))
        assert public_release['public_verification_metadata']['public_key_hex'] == release.public_key_hex
        assert 'private_key_seed_32b' not in public_release
    # A changed segment can no longer inherit the old screening approval.
    original = source.read_bytes()
    try:
        source.write_bytes(original + b'changed')
        with pytest.raises(ValueError, match='changed'):
            packaging_for_asset(asset)
        changed = service.start_screening_job(tenant_id=tenant, asset_id=asset.asset_id)
        assert changed.status == 'FAILED'
    finally:
        source.write_bytes(original)
