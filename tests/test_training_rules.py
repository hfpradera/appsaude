from datetime import date

from app.models import DailyRecovery, Sleep, SubjectiveCheckin
from app.services.reports import classify_day


def test_red_flag_forces_rest_day():
    checkin = SubjectiveCheckin(user_id=1, day=date.today(), red_flags="dor no peito", energy=8)
    result = classify_day(None, None, checkin, 0, 0)
    assert result.label == "descanso"
    assert result.warnings


def test_good_recovery_allows_quality_but_not_medical_clearance():
    recovery = DailyRecovery(user_id=1, day=date.today(), recovery_score=80, hrv_ms=60, resting_hr=48)
    sleep = Sleep(user_id=1, day=date.today(), sleep_duration_seconds=8 * 3600)
    checkin = SubjectiveCheckin(user_id=1, day=date.today(), energy=8, muscle_soreness=1)
    result = classify_day(recovery, sleep, checkin, 2 * 3600, 10 * 3600)
    assert result.label == "qualidade"
    assert all("clinicamente" not in reason.lower() for reason in result.reasons)
