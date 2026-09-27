"""Quality reports never turn absent/truncated/skipped results into a pass."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("quality_report", Path(__file__).parents[1] / "scripts" / "quality-report.py")
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)

TAP = """TAP version 13
# Subtest: fake tap
ok 1 - fake tap
  ---
  duration_ms: 4.2
  type: 'test'
  ...
# Subtest: optional fake
ok 2 - optional fake # SKIP platform
  ---
  duration_ms: 0.1
  type: 'test'
  ...
1..2
# tests 2
# suites 0
# pass 1
# fail 0
# cancelled 0
# skipped 1
# todo 0
# duration_ms 15.2
"""


def test_junit_tracks_failure_error_skip_and_duration(tmp_path):
    file = tmp_path / "junit.xml"
    file.write_text('''<testsuites><testsuite tests="4" failures="1" errors="1" time="2.1">
        <testcase classname="tests.test_phone" name="works" time=".1"/>
        <testcase classname="tests.test_desktop" name="breaks" time=".2"><failure/></testcase>
        <testcase classname="tests.test_transfers" name="broken" time=".3"><error/></testcase>
        <testcase classname="tests.test_operator_progress" name="restart" time="0"><skipped message="optional SDK"/></testcase>
        </testsuite></testsuites>''')
    parsed = quality.parse_junit(file)
    stats = quality.totals(parsed["cases"])
    assert stats == {"total": 4, "passed": 1, "failed": 1, "errors": 1, "cancelled": 0, "skipped": 1, "sum_case_seconds": .6}
    assert parsed["recorded_suite_seconds"] == 2.1
    assert parsed["cases"][-1]["skip_reason"] == "optional SDK"


def test_windows_utf16_tap_is_supported_and_skips_are_not_passes(tmp_path):
    file = tmp_path / "node.tap"
    file.write_text(TAP, encoding="utf-16")
    parsed = quality.parse_tap(file, {"fake tap": "tests/test_desktop_touch.cjs"})
    assert len(parsed["cases"]) == 2
    assert parsed["cases"][0]["group"] == "input"
    assert parsed["cases"][0]["seconds"] == pytest.approx(.0042)
    assert quality.totals(parsed["cases"])["passed"] == 1
    assert quality.totals(parsed["cases"])["skipped"] == 1


@pytest.mark.parametrize("body", [TAP.split("1..2")[0], TAP.replace("# tests 2", "# tests 3"),
                                  TAP.replace("# pass 1", "# pass 2"), "Bail out! crashed"])
def test_truncated_or_inconsistent_tap_is_rejected(tmp_path, body):
    file = tmp_path / "bad.tap"
    file.write_text(body)
    with pytest.raises(ValueError):
        quality.parse_tap(file)


def test_empty_junit_or_entities_do_not_count_as_success(tmp_path):
    file = tmp_path / "empty.xml"
    for content in ('<testsuite tests="0"/>', '<testsuite tests="1"/>', '<!DOCTYPE a><testsuite tests="0"/>'):
        file.write_text(content)
        with pytest.raises(ValueError):
            quality.parse_junit(file)


def test_missing_results_have_no_score_and_failure_exit(tmp_path):
    report = quality.build_report([quality.collect(tmp_path / "missing.xml", quality.parse_junit, "pytest"),
                                   quality.collect(tmp_path / "missing.tap", quality.parse_tap, "node")], {})
    assert report["status"] == "incomplete"
    assert report["reliability_pass_percent"] is None
    assert not report["complete_inputs"]


def complete_inputs():
    cases = [{"name": group, "group": group, "status": "passed", "seconds": .1, "source": "fake", "runner": "pytest"}
             for group in quality.GROUPS if group != "other"]
    return [{"runner": "pytest", "complete": True, "cases": cases},
            {"runner": "node", "complete": True, "cases": []}]


def test_score_denominator_excludes_but_discloses_skips():
    inputs = complete_inputs()
    inputs[0]["cases"] += [{"name": "skip", "group": "auth", "status": "skipped", "seconds": 0, "source": "fake", "runner": "pytest"},
                            {"name": "failure", "group": "input", "status": "failed", "seconds": .1, "source": "fake", "runner": "pytest"}]
    report = quality.build_report(inputs, {})
    assert report["status"] == "failed"
    assert report["reliability_pass_percent"] == 83.33
    assert report["totals"]["skipped"] == 1
    assert "لا تعني النجاح" in quality.markdown(report)


def test_runner_failure_cannot_reuse_a_success_score():
    report = quality.build_report(complete_inputs(), {}, {"environment": {}, "runs": [{"returncode": 2}]})
    assert report["status"] == "incomplete" and report["reliability_pass_percent"] is None
