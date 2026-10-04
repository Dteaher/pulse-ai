from app.telemetry import PerformanceProfile, corrective_metrics


def test_corrective_rates_exclude_cache_and_have_explicit_denominators():
    results = corrective_metrics([
        {'patch_corrective_count': 1, 'patch_success_count': 1, 'patch_output_tokens': 160, 'full_corrective_output_tokens': 0},
        {'patch_corrective_count': 1, 'patch_rejected_quality_count': 1, 'patch_output_tokens': 100, 'full_corrective_count': 1, 'full_corrective_output_tokens': 5000},
        {'patch_corrective_count': 1, 'patch_success_count': 1, 'cache_hit': True},
        {'full_corrective_count': 1, 'deduplicated': True},
    ])
    assert results == {'request_count': 2, 'patch_attempt_count': 2, 'full_corrective_rate': 0.5,
                       'patch_corrective_rate': 1, 'patch_success_rate': 0.5,
                       'patch_rejected_quality_rate': 0.5, 'average_patch_output_tokens': 130,
                       'full_corrective_output_tokens': 5000}


def test_no_patch_observations_never_claim_zero_percent_success():
    result = corrective_metrics([])
    assert result['patch_success_rate'] is None and result['full_corrective_rate'] is None
    assert result['average_patch_output_tokens'] is None


def test_unknown_token_usage_not_reported_as_zero_or_complete():
    p = PerformanceProfile(calls=[{'operation': 'structural_repair', 'duration_ms': 100, 'output_tokens': None}], patch_corrective_count=1)
    report = p.report()
    assert report['patch_output_tokens'] is None and not report['usage_complete']
    assert corrective_metrics([report])['average_patch_output_tokens'] is None
