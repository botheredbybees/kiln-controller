import config


def test_listening_port():
    assert config.listening_port == 9099


def test_cost_settings():
    assert config.kwh_rate == 0.1319
    assert config.kw_elements == 9.460
    assert config.currency_type == "$"


def test_sensor_settings():
    assert config.sensor_time_wait == 2
    assert config.temperature_average_samples == 10


def test_throttle_settings():
    assert config.throttle_below_temp == 300
    assert config.throttle_percent == 20


def test_emergency_shutoff():
    assert config.emergency_shutoff_temp == 2264
    assert config.ignore_temp_too_high is False


def test_emergency_heat_rate():
    assert config.emergency_heat_rate == 23
    assert config.emergency_heat_rate_window == 22.5
    assert config.ignore_heat_rate_too_low is False


def test_temp_scale():
    assert config.temp_scale == "f"


def test_thermocouple_selection():
    assert config.max31855 == 1
    assert config.max31856 == 0


def test_automatic_restart_settings():
    assert config.automatic_restarts is True
    assert config.automatic_restart_window == 15


def test_alert_thresholds():
    """thresholds tuned in the alerts section of config.py. ids line up
    with the alert registry in lib/alerts.py"""
    assert config.cooled_safe_temp == 150
    assert config.relay_stuck_on_rise == 25
    assert config.relay_stuck_on_window == 10
    assert config.temp_implausible_jump == 50
    assert config.tc_error_percent_limit == 30
    assert config.catch_up_stalled_minutes == 15
    assert config.alert_webhook_timeout == 10


def test_profiles_directory_points_at_storage():
    assert config.kiln_profiles_directory.endswith("storage" + config.os.sep + "profiles")


def test_supervisor_settings_ship_off_and_in_display_scale():
    """the supervisor is off by default. each delta is in the display scale
    (the repo ships in F), 0 turns a rule off."""
    assert config.supervisor_enabled is False
    assert config.supervisor_peak_margin == 54  # 30 C
    assert config.supervisor_over_target_heater_on == 18  # 10 C
    assert config.supervisor_over_target_heater_on_seconds == 20
    assert config.supervisor_over_target == 45  # 25 C
    assert config.supervisor_over_target_seconds == 120
    assert config.supervisor_below_target == 72  # 40 C
    assert config.supervisor_below_target_minutes == 10
    assert config.supervisor_max_rise_per_minute == 54  # 30 C/min
    assert config.supervisor_stuck_minutes == 12
    assert config.supervisor_stuck_rise == 11  # 6 C
    assert config.supervisor_no_rise_minutes == 10
    assert config.supervisor_no_rise_gain == 5  # 3 C
    assert config.supervisor_plateau_minutes == 20
    assert config.supervisor_plateau_gain == 9  # 5 C
    assert config.supervisor_plateau_hold_minutes == 30
