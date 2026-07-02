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
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
OLD_GAUSSDB_WORDING = "ext" + "ernal GaussDB"


def _helm_template(*set_values):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("helm is required to render chart templates")

    cmd = [helm, "template", "ragflow", str(ROOT / "helm"), "--namespace", "ragflow"]
    for value in set_values:
        cmd.extend(["--set", value])
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)


def test_docker_env_lists_gaussdb_as_doc_engine():
    env_text = (ROOT / "docker" / ".env").read_text(encoding="utf-8")
    assert "gaussdb" in env_text
    assert "GAUSSDB_HOST" in env_text
    assert "GAUSSDB_SCHEMA" in env_text
    assert OLD_GAUSSDB_WORDING not in env_text
    env_values = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", env_text, re.MULTILINE))
    assert env_values["GAUSSDB_DATABASE"]
    for key in (
        "GAUSSDB_HOST",
        "GAUSSDB_PORT",
        "GAUSSDB_DATABASE",
        "GAUSSDB_USER",
        "GAUSSDB_PASSWORD",
        "GAUSSDB_SCHEMA",
    ):
        assert re.search(rf"# .+\n{key}=", env_text)


def test_docker_readme_does_not_default_gaussdb_port():
    text = (ROOT / "docker" / "README.md").read_text(encoding="utf-8")
    gaussdb_section = text.split("- `gaussdb`", 1)[1].split("###", 1)[0]
    assert "Defaults to `19995`" not in gaussdb_section
    assert OLD_GAUSSDB_WORDING not in gaussdb_section


def test_service_conf_template_renders_gaussdb_config_block():
    text = (ROOT / "docker" / "service_conf.yaml.template").read_text(encoding="utf-8")
    assert "gaussdb:" in text
    gaussdb_block = text.split("gaussdb:", 1)[1].split("seekdb:", 1)[0]
    assert "host: '${GAUSSDB_HOST" in gaussdb_block
    assert "port: ${GAUSSDB_PORT}" in gaussdb_block
    assert "database: '${GAUSSDB_DATABASE" in gaussdb_block
    assert "schema: '${GAUSSDB_SCHEMA" in gaussdb_block
    assert "scheme:" not in gaussdb_block


def test_helm_env_allows_gaussdb_without_internal_service():
    text = (ROOT / "helm" / "templates" / "env.yaml").read_text(encoding="utf-8")
    assert 'eq .Values.env.DOC_ENGINE "gaussdb"' in text
    assert "GAUSSDB_HOST" in text
    assert "must be either" in text
    assert "gaussdb" in text


def test_helm_docs_use_instance_wording_for_gaussdb():
    for relative_path in ("helm/README.md", "helm/values.yaml"):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert OLD_GAUSSDB_WORDING not in text


def test_helm_values_show_gaussdb_schema_default():
    text = (ROOT / "helm" / "values.yaml").read_text(encoding="utf-8")
    gaussdb_block = text.split("GaussDB DocEngine connection.", 1)[1].split("# The password for MySQL", 1)[0]
    assert 'GAUSSDB_HOST: ""' in gaussdb_block
    assert 'GAUSSDB_PORT: ""' in gaussdb_block
    assert "GAUSSDB_DATABASE: postgres" in gaussdb_block
    assert 'GAUSSDB_USER: ""' in gaussdb_block
    assert 'GAUSSDB_PASSWORD: ""' in gaussdb_block
    assert "GAUSSDB_SCHEMA: public" in gaussdb_block


def test_helm_env_does_not_duplicate_gaussdb_values_from_generic_loop():
    text = (ROOT / "helm" / "templates" / "env.yaml").read_text(encoding="utf-8")
    generic_loop = text.split("{{- range $key, $val := .Values.env }}", 1)[1].split("{{- end }}", 1)[0]
    for key in (
        "GAUSSDB_HOST",
        "GAUSSDB_PORT",
        "GAUSSDB_DATABASE",
        "GAUSSDB_USER",
        "GAUSSDB_PASSWORD",
        "GAUSSDB_SCHEMA",
    ):
        assert f'(ne $key "{key}")' in generic_loop
    assert 'GAUSSDB_PORT: {{ .Values.env.GAUSSDB_PORT | required "GAUSSDB_PORT is required when DOC_ENGINE=gaussdb" | quote }}' in text
    assert 'GAUSSDB_USER: {{ .Values.env.GAUSSDB_USER | required "GAUSSDB_USER is required when DOC_ENGINE=gaussdb" | quote }}' in text
    assert 'GAUSSDB_PASSWORD: {{ .Values.env.GAUSSDB_PASSWORD | required "GAUSSDB_PASSWORD is required when DOC_ENGINE=gaussdb" | quote }}' in text
    assert 'GAUSSDB_SCHEMA: {{ default "public" .Values.env.GAUSSDB_SCHEMA | quote }}' in text


def test_helm_template_defaults_gaussdb_database_and_schema():
    result = _helm_template(
        "env.DOC_ENGINE=gaussdb",
        "env.GAUSSDB_HOST=gaussdb.local",
        "env.GAUSSDB_PORT=19995",
        "env.GAUSSDB_USER=ragflow",
        "env.GAUSSDB_PASSWORD=secret",
    )

    assert result.returncode == 0, result.stderr
    assert 'GAUSSDB_DATABASE: "postgres"' in result.stdout
    assert 'GAUSSDB_SCHEMA: "public"' in result.stdout


def test_helm_template_preserves_custom_gaussdb_database_and_schema():
    result = _helm_template(
        "env.DOC_ENGINE=gaussdb",
        "env.GAUSSDB_HOST=gaussdb.local",
        "env.GAUSSDB_PORT=19995",
        "env.GAUSSDB_DATABASE=ragflow_doc",
        "env.GAUSSDB_USER=ragflow",
        "env.GAUSSDB_PASSWORD=secret",
        "env.GAUSSDB_SCHEMA=ragflow_schema",
    )

    assert result.returncode == 0, result.stderr
    assert 'GAUSSDB_DATABASE: "ragflow_doc"' in result.stdout
    assert 'GAUSSDB_SCHEMA: "ragflow_schema"' in result.stdout


@pytest.mark.parametrize(
    ("missing_key", "message"),
    [
        ("GAUSSDB_HOST", "GAUSSDB_HOST is required when DOC_ENGINE=gaussdb"),
        ("GAUSSDB_PORT", "GAUSSDB_PORT is required when DOC_ENGINE=gaussdb"),
        ("GAUSSDB_USER", "GAUSSDB_USER is required when DOC_ENGINE=gaussdb"),
        ("GAUSSDB_PASSWORD", "GAUSSDB_PASSWORD is required when DOC_ENGINE=gaussdb"),
    ],
)
def test_helm_template_requires_gaussdb_connection_values(missing_key, message):
    values = {
        "GAUSSDB_HOST": "gaussdb.local",
        "GAUSSDB_PORT": "19995",
        "GAUSSDB_USER": "ragflow",
        "GAUSSDB_PASSWORD": "secret",
    }
    values.pop(missing_key)

    result = _helm_template(
        "env.DOC_ENGINE=gaussdb",
        *(f"env.{key}={value}" for key, value in values.items()),
    )

    assert result.returncode != 0
    assert message in result.stderr
