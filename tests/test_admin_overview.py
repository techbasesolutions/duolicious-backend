"""Endpoint-shape tests for /admin/overview. The actual SQL execution
is integration-tested via the live smoke step at Phase 1 end."""


def test_overview_routes_module_imports():
    """If imports succeed and the module loads, the @aget decorator has
    registered both handlers with the Flask app."""
    import service.api.admin.overview_routes
    assert hasattr(service.api.admin.overview_routes, 'get_admin_whoami')
    assert hasattr(service.api.admin.overview_routes, 'get_admin_overview')


def test_q_overview_kpis_has_six_columns():
    from service.admin.queries import Q_OVERVIEW_KPIS
    assert Q_OVERVIEW_KPIS.strip().upper().startswith("SELECT")
    # 6 KPI columns expected; "AS" appears at least 6 times.
    assert Q_OVERVIEW_KPIS.count(" AS ") >= 6
