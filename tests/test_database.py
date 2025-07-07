import asyncio
from unittest.mock import MagicMock

import database


def test_get_investigation_projects_filters_by_org(monkeypatch):
    execute_result = MagicMock()
    execute_result.data = []

    eq_mock = MagicMock()
    result_obj = MagicMock()
    result_obj.execute = MagicMock(return_value=execute_result)
    result_obj.eq = eq_mock
    eq_mock.return_value = result_obj

    select_obj = MagicMock(eq=eq_mock)
    select_mock = MagicMock(return_value=select_obj)
    table_mock = MagicMock(return_value=MagicMock(select=select_mock))
    client_mock = MagicMock(table=table_mock)
    monkeypatch.setattr(database, "supabase_client", client_mock)

    asyncio.run(database.get_investigation_projects(1, organization="OrgX"))

    eq_mock.assert_any_call("organization", "OrgX")
