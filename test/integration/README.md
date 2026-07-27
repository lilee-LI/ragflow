# GaussDB live integration tests

The GaussDB integration suite is opt-in because it creates and removes real
database objects. Set `GAUSSDB_INTEGRATION=1` to enable it. Without that flag,
fixtures that require a live GaussDB skip their tests.

## Required configuration

Set the connection and deployment variables for the target environment:

```text
GAUSSDB_INTEGRATION=1
GAUSSDB_HOST=<host>
GAUSSDB_PORT=<port, defaults to 19995>
GAUSSDB_DATABASE=<database>
GAUSSDB_USER=<user>
GAUSSDB_PASSWORD=<password>
GAUSSDB_SCHEMA=<pre-created test schema>
GAUSSDB_VARIANT=<centralized|distributed>
GAUSSDB_EXPECTED_VECTOR_DIMS=<comma-separated dimensions>
```

`GAUSSDB_SCHEMA` is read from the environment and has no required fixed name.
It must name an existing schema on which `GAUSSDB_USER` has `USAGE` and
`CREATE`. Use a dedicated test schema: the suite creates tables there and
removes the generated tables during cleanup, but it does not create or drop the
schema itself.

The expected vector dimensions are deployment-specific:

```text
centralized: 1024,1536,3072
distributed: 1024
```

The preflight also verifies the configured database and user, read-write mode,
UStore, VectorDB, SQL compatibility, and the expected deployment topology.
HTTP integration cases require a running RAGFlow service; set
`RAGFLOW_BASE_URL` when it is not available at `http://127.0.0.1`.
