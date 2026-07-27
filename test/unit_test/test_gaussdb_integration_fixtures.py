#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore", pytest.PytestUnknownMarkWarning)
    from test.integration import conftest as gaussdb_it_fixtures


def test_assert_schema_access_reports_missing_schema_before_privilege_probe(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            assert "has_schema_privilege" not in query
            assert params == ["missing_schema"]

        def fetchone(self):
            return (False,)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(gaussdb_it_fixtures, "_admin_conn", lambda: Connection())

    with pytest.raises(AssertionError, match="must be pre-created"):
        gaussdb_it_fixtures._assert_schema_access("missing_schema")


def test_require_gaussdb_schema_accepts_environment_value(monkeypatch):
    monkeypatch.setenv("GAUSSDB_SCHEMA", "team_specific_integration_schema")

    assert gaussdb_it_fixtures._require_gaussdb_schema() == "team_specific_integration_schema"


def test_require_gaussdb_schema_rejects_missing_or_blank_value(monkeypatch):
    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv("GAUSSDB_SCHEMA", raising=False)
        else:
            monkeypatch.setenv("GAUSSDB_SCHEMA", value)

        with pytest.raises(pytest.fail.Exception, match="set GAUSSDB_SCHEMA to a pre-created integration schema"):
            gaussdb_it_fixtures._require_gaussdb_schema()
