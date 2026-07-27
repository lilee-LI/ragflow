param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("centralized", "distributed")]
    [string]$Variant,
    [Parameter(Mandatory = $true)]
    [string]$ResultName,
    [Parameter(Mandatory = $true)]
    [string]$NodeList,
    [switch]$ForceRecreate
)

$ErrorActionPreference = "Stop"

function Get-ComposeConfigHashes {
    $output = & docker compose -f $composeFile config --hash '*' 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot calculate compose config hashes: $($output -join [Environment]::NewLine)"
    }
    $hashes = @{}
    foreach ($line in $output) {
        if ($line -match '^(\S+)\s+([0-9a-f]{64})$') {
            $hashes[$matches[1]] = $matches[2]
        }
    }
    foreach ($service in @("gaussdb-proxy", "tei", "ragflow-cpu")) {
        if (-not $hashes.ContainsKey($service)) {
            throw "Missing compose config hash for $service"
        }
    }
    return $hashes
}

function Test-CompatibleEnvironment {
    param([hashtable]$ExpectedHashes)

    $containers = @{
        "gaussdb-proxy" = "gaussdb-e2e-dual-proxy"
        "tei" = "gaussdb-e2e-dual-tei"
        "ragflow-cpu" = "gaussdb-e2e-dual-ragflow"
    }
    $reasons = [System.Collections.Generic.List[string]]::new()
    foreach ($service in $containers.Keys) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $inspectJson = & docker inspect $containers[$service] 2>$null
        $inspectExitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if ($inspectExitCode -ne 0) {
            $reasons.Add("$service container is missing")
            continue
        }
        $inspect = @($inspectJson | ConvertFrom-Json)[0]
        if (-not $inspect.State.Running) {
            $reasons.Add("$service container is not running")
        }
        if ($service -ne "ragflow-cpu") {
            $actualHash = $inspect.Config.Labels.'com.docker.compose.config-hash'
            if ($actualHash -ne $ExpectedHashes[$service]) {
                $reasons.Add("$service config hash differs")
            }
            continue
        }

        # Compose 5.3.1 calculates a different `config --hash` for services
        # containing `build:` than the hash stored on a --no-build container.
        # Compare the effective RAGFlow runtime contract instead.
        $actualEnv = @{}
        foreach ($entry in $inspect.Config.Env) {
            $parts = $entry -split "=", 2
            $actualEnv[$parts[0]] = if ($parts.Count -eq 2) { $parts[1] } else { "" }
        }
        $expectedEnv = @{
            "DOC_ENGINE" = "gaussdb"
            "DB_TYPE" = "gaussdb"
            "METADATA_DB_PROFILE" = "gaussdb"
            "GAUSSDB_METADATA_SCHEMA" = $env:GAUSSDB_E2E_METADATA_SCHEMA
            "GAUSSDB_METADATA_HOST" = $env:GAUSSDB_UPSTREAM_HOST
            "GAUSSDB_METADATA_PORT" = $env:GAUSSDB_UPSTREAM_PORT
            "GAUSSDB_VARIANT" = $Variant
            "GAUSSDB_EXPECTED_VECTOR_DIMS" = $env:GAUSSDB_EXPECTED_VECTOR_DIMS
            "EMBEDDING_BATCH_SIZE" = $embeddingBatchSize
            "CHUNK_FEEDBACK_ENABLED" = $env:CHUNK_FEEDBACK_ENABLED
            "CHUNK_FEEDBACK_WEIGHTING" = $env:CHUNK_FEEDBACK_WEIGHTING
            "TEI_MODEL" = $env:TEI_MODEL
            "GAUSSDB_E2E_SOURCE_REVISION" = $env:GAUSSDB_E2E_SOURCE_REVISION
        }
        foreach ($name in $expectedEnv.Keys) {
            if ($actualEnv[$name] -ne $expectedEnv[$name]) {
                $reasons.Add("ragflow-cpu $name differs")
            }
        }
        $expectedImage = if ($env:RAGFLOW_E2E_IMAGE) {
            $env:RAGFLOW_E2E_IMAGE
        }
        else {
            "ragflow-gaussdb-e2e-dual:v0.26.4-deps"
        }
        if ($inspect.Config.Image -ne $expectedImage) {
            $reasons.Add("ragflow-cpu image differs")
        }
        $codeMount = @($inspect.Mounts | Where-Object { $_.Destination -eq "/e2e-code" })[0]
        if (-not $codeMount -or -not [string]::Equals(
            [IO.Path]::GetFullPath($codeMount.Source).TrimEnd("\"),
            [IO.Path]::GetFullPath($worktree).TrimEnd("\"),
            [StringComparison]::OrdinalIgnoreCase
        )) {
            $reasons.Add("ragflow-cpu worktree mount differs")
        }
    }
    try {
        $health = Invoke-RestMethod -Uri "$($env:RAGFLOW_BASE_URL)/api/v1/system/healthz" -TimeoutSec 5
        if ($health.status -ne "ok") {
            $reasons.Add("RAGFlow health status is not ok")
        }
    }
    catch {
        $reasons.Add("RAGFlow health endpoint is unavailable")
    }
    return [pscustomobject]@{
        Compatible = $reasons.Count -eq 0
        Reasons = @($reasons)
    }
}

function Get-ContainerEnvironment {
    param([string]$Container)

    $inspect = @((& docker inspect $Container 2>$null | ConvertFrom-Json))[0]
    if ($LASTEXITCODE -ne 0 -or -not $inspect) {
        throw "Cannot inspect $Container"
    }
    $values = @{}
    foreach ($entry in $inspect.Config.Env) {
        $parts = $entry -split "=", 2
        $values[$parts[0]] = if ($parts.Count -eq 2) { $parts[1] } else { "" }
    }
    return [pscustomobject]@{
        Image = $inspect.Config.Image
        ConfigHash = $inspect.Config.Labels.'com.docker.compose.config-hash'
        Values = $values
    }
}

function Ensure-DependencyContainers {
    param([string]$LogPath)

    $dependencies = @("docker-redis-1", "docker-minio-1")
    foreach ($container in $dependencies) {
        $inspectJson = & docker inspect $container 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Required dependency container is missing: $container"
        }
        $inspect = @($inspectJson | ConvertFrom-Json)[0]
        if (-not $inspect.State.Running) {
            & docker start $container 1>> $LogPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "Cannot start required dependency container: $container"
            }
        }
    }

    $deadline = (Get-Date).AddMinutes(10)
    do {
        $notReady = @()
        foreach ($container in $dependencies) {
            $inspect = @((& docker inspect $container 2>$null | ConvertFrom-Json))[0]
            $health = if ($inspect.State.Health) { $inspect.State.Health.Status } else { "running" }
            if (-not $inspect.State.Running -or $health -ne "healthy") {
                $notReady += "$container=$health"
            }
        }
        if ($notReady.Count -eq 0) {
            return
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    throw "Required dependency containers did not become healthy: $($notReady -join ', ')"
}

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Content
    )

    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

$worktree = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $worktree
$sourcePaths = @("admin", "agent", "api", "common", "deepdoc", "memory", "mcp", "ragflow_deps", "tools", "rag", "conf", "pyproject.toml")
$sourceHead = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Cannot determine E2E source HEAD"
}
$sourceDiff = & git diff --no-ext-diff --binary HEAD -- @sourcePaths | git hash-object --stdin
if ($LASTEXITCODE -ne 0) {
    throw "Cannot calculate E2E source diff fingerprint"
}
$env:GAUSSDB_E2E_SOURCE_REVISION = "$sourceHead-$($sourceDiff.Trim())"
$guidePath = if ($env:GAUSSDB_E2E_GUIDE_PATH) {
    $env:GAUSSDB_E2E_GUIDE_PATH
}
else {
    "D:\RAGFlow\GaussDB_environment_access_guide.md"
}
$guide = Get-Content -LiteralPath $guidePath -Encoding UTF8 -Raw

$gaussdbPasswordMatch = [regex]::Match($guide, '(?m)^\s*GAUSSDB_PASSWORD\s*=\s*["'']?([^"''\s]+)')
if (-not $gaussdbPasswordMatch.Success) {
    throw "Cannot parse GaussDB password from environment guide"
}
$env:GAUSSDB_PASSWORD = $gaussdbPasswordMatch.Groups[1].Value
$zhipuKeyMatch = [regex]::Match($guide, '(?m)^\s*(?:export\s+)?ZHIPU_AI_API_KEY\s*=\s*["'']?([^"''\s]+)')
if (-not $zhipuKeyMatch.Success) {
    throw "Cannot parse Zhipu key from environment guide"
}
$env:ZHIPU_AI_API_KEY = $zhipuKeyMatch.Groups[1].Value

$env:RAGFLOW_BASE_URL = "http://127.0.0.1:28080"
$env:SEEDED_USER_EMAIL = "gaussdb.e2e.dual@infiniflow.org"
$env:SEEDED_USER_PASSWORD = "123"
$env:GAUSSDB_DATABASE = "docgine_gaussdb_zlw"
$env:GAUSSDB_USER = "zlw"
$env:GAUSSDB_SCHEMA = "zlw"
$env:GAUSSDB_E2E_METADATA_SCHEMA = "zlw_e2e_metadata_d1a110e"
$env:GAUSSDB_VARIANT = $Variant
$env:DOC_ENGINE = "gaussdb"
$env:DB_TYPE = "gaussdb"
$env:METADATA_DB_PROFILE = "gaussdb"
$env:COMPOSE_PROFILES = "gaussdb,cpu,tei-cpu,metadata-gaussdb"
$env:TEI_HOST = "tei"
$env:TEI_MODEL = "BAAI/bge-large-en-v1.5"
$nodes = @($NodeList.Split(";", [System.StringSplitOptions]::RemoveEmptyEntries))
if ($nodes.Count -eq 0) {
    throw "NodeList must contain at least one explicit pytest node"
}
$tcIds = @(
    foreach ($node in $nodes) {
        if ($node -notmatch '::test_tc_e2e_(\d+)_') {
            throw "NodeList contains an unnumbered or invalid GaussDB E2E node: $node"
        }
        $matches[1]
    }
)
$feedbackNodes = @($tcIds | Where-Object { $_ -in @("801", "802") })
if ($feedbackNodes.Count -gt 0 -and $feedbackNodes.Count -ne $tcIds.Count) {
    throw "Feedback TC801/802 must not be mixed with the normal profile"
}
foreach ($exclusiveId in @("1101", "1105")) {
    if ($exclusiveId -in $tcIds -and $tcIds.Count -ne 1) {
        throw "TC$exclusiveId requires an exclusive runner invocation"
    }
}
$env:CHUNK_FEEDBACK_ENABLED = if ($feedbackNodes.Count -gt 0) { "true" } else { "false" }
$env:CHUNK_FEEDBACK_WEIGHTING = "uniform"
$env:GAUSSDB_E2E_PASSWORD = $env:GAUSSDB_PASSWORD
$embeddingBatchSize = if ($env:GAUSSDB_E2E_EMBEDDING_BATCH_SIZE) { $env:GAUSSDB_E2E_EMBEDDING_BATCH_SIZE } else { "8" }
if ($embeddingBatchSize -notin @("4", "8")) {
    throw "GAUSSDB_E2E_EMBEDDING_BATCH_SIZE must be 8 or 4"
}
$env:GAUSSDB_E2E_EMBEDDING_BATCH_SIZE = $embeddingBatchSize
if ($Variant -eq "centralized") {
    $env:GAUSSDB_HOST = "121.37.186.131"
    $env:GAUSSDB_PORT = "19995"
    $env:GAUSSDB_EXPECTED_VECTOR_DIMS = "1024,1536,3072"
    $env:GAUSSDB_UPSTREAM_HOST = "121.37.186.131"
    $env:GAUSSDB_UPSTREAM_PORT = "19995"
}
else {
    $env:GAUSSDB_HOST = "127.0.0.1"
    $env:GAUSSDB_PORT = "18000"
    $env:GAUSSDB_EXPECTED_VECTOR_DIMS = "1024"
    $env:GAUSSDB_UPSTREAM_HOST = "host.docker.internal"
    $env:GAUSSDB_UPSTREAM_PORT = "18000"
    $env:GAUSSDB_E2E_QUERY_CONTAINER = "gaussdb-e2e-dual-ragflow"
    $env:GAUSSDB_E2E_QUERY_HOST = $env:GAUSSDB_UPSTREAM_HOST
    $env:GAUSSDB_E2E_QUERY_PORT = $env:GAUSSDB_UPSTREAM_PORT
}
$composeDir = Join-Path $worktree "docker"
$composeFile = Join-Path $composeDir "docker-compose.gaussdb-dual-e2e.yml"
$env:GAUSSDB_E2E_COMPOSE_DIR = $composeDir
$env:COMPOSE_FILE = "docker-compose.gaussdb-dual-e2e.yml"
$env:GAUSSDB_E2E_RESTART_COMMAND = "docker compose up -d --no-deps --force-recreate ragflow-cpu"
if ($NodeList -match 'test_tc_e2e_1101_') {
    $env:GAUSSDB_FAULT_INJECTION = "1"
    $env:GAUSSDB_E2E_FAULT_COMMAND = "docker stop -t 0 gaussdb-e2e-dual-proxy"
    $env:GAUSSDB_E2E_RECOVER_COMMAND = "docker start gaussdb-e2e-dual-proxy"
}

$env:PYTHONPATH = $worktree
$env:PW_HEADLESS = "true"
$env:PW_TRACE = "true"
$env:PW_FIXTURE_DEBUG = "true"
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
$env:PLAYWRIGHT_ACTION_TIMEOUT_MS = "60000"
$suiteTimeoutSeconds = if ($env:GAUSSDB_E2E_SUITE_TIMEOUT_S) {
    [int]$env:GAUSSDB_E2E_SUITE_TIMEOUT_S
}
else {
    21600
}
if ($suiteTimeoutSeconds -lt 600) {
    throw "GAUSSDB_E2E_SUITE_TIMEOUT_S must be at least 600 seconds"
}
$env:PLAYWRIGHT_HANG_TIMEOUT_S = [string]($suiteTimeoutSeconds - 60)
$env:GAUSSDB_E2E_CHAT_TIMEOUT_MS = "1200000"
$env:GAUSSDB_E2E_LONG_RUNNING_TIMEOUT_MS = "21600000"

$resultDir = Join-Path $worktree "test\playwright\artifacts\e2e\gaussdb-dual\$Variant\$ResultName"
New-Item -ItemType Directory -Force -Path $resultDir | Out-Null
$stdout = Join-Path $resultDir "pytest.stdout.log"
$stderr = Join-Path $resultDir "pytest.stderr.log"
$junit = Join-Path $resultDir "junit.xml"
$exitCodeFile = Join-Path $resultDir "exit-code.txt"
$cudaPreflight = Join-Path $resultDir "cuda-preflight.log"
$composePreflight = Join-Path $resultDir "compose-preflight.log"
$modelPreflight = Join-Path $resultDir "chat-model-preflight.log"
$watchdogLog = Join-Path $resultDir "watchdog.log"
$runtimeProfile = Join-Path $resultDir "runtime-profile.json"
$python = if ($env:GAUSSDB_E2E_PYTHON) {
    $env:GAUSSDB_E2E_PYTHON
}
else {
    Join-Path $worktree ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime not found: $python"
}
if (Test-Path -LiteralPath $exitCodeFile) {
    Remove-Item -LiteralPath $exitCodeFile -Force
}

$ErrorActionPreference = "Continue"
& nvidia-smi -L 1> $cudaPreflight 2>&1
if ($LASTEXITCODE -ne 0) {
    Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
    exit $LASTEXITCODE
}
$cudaHealthy = $false
try {
    $cudaHealth = Invoke-RestMethod -Uri "http://127.0.0.1:18080/health" -TimeoutSec 5
    $cudaHealthy = (
        $cudaHealth.status -eq "ok" -and
        $cudaHealth.provider -eq "CUDAExecutionProvider" -and
        $cudaHealth.model -eq "BAAI/bge-large-en-v1.5" -and
        [int]$cudaHealth.dimensions -eq 1024
    )
}
catch {
    $cudaHealthy = $false
}
if (-not $cudaHealthy) {
    try {
        $ErrorActionPreference = "Stop"
        $cudaStartScript = if ($env:GAUSSDB_E2E_CUDA_START_SCRIPT) {
            $env:GAUSSDB_E2E_CUDA_START_SCRIPT
        }
        else {
            "D:\RAGFlow\e2e-env\start_tei_cuda.ps1"
        }
        & $cudaStartScript 1>> $cudaPreflight 2>&1
        $cudaHealth = Invoke-RestMethod -Uri "http://127.0.0.1:18080/health" -TimeoutSec 30
        if (
            $cudaHealth.status -ne "ok" -or
            $cudaHealth.provider -ne "CUDAExecutionProvider" -or
            $cudaHealth.model -ne "BAAI/bge-large-en-v1.5" -or
            [int]$cudaHealth.dimensions -ne 1024
        ) {
            throw "CUDA TEI health contract mismatch"
        }
    }
    catch {
        $_.Exception.Message | Add-Content -LiteralPath $cudaPreflight -Encoding UTF8
        Set-Content -LiteralPath $exitCodeFile -Value 1 -Encoding ascii
        exit 1
    }
}
$ErrorActionPreference = "Continue"
& $python (Join-Path $worktree "docker\gpu-embedding-proof.py") 1>> $cudaPreflight 2>&1
if ($LASTEXITCODE -ne 0) {
    Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
    exit $LASTEXITCODE
}

$ErrorActionPreference = "Stop"
Ensure-DependencyContainers -LogPath $composePreflight
if (-not $env:RAGFLOW_E2E_IMAGE) {
    $ErrorActionPreference = "Continue"
    & docker compose -f $composeFile build ragflow-cpu 1>> $composePreflight 2>&1
    if ($LASTEXITCODE -ne 0) {
        Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
        exit $LASTEXITCODE
    }
    $ErrorActionPreference = "Stop"
}
$expectedHashes = Get-ComposeConfigHashes
$compatibility = Test-CompatibleEnvironment -ExpectedHashes $expectedHashes
$environmentReused = -not $ForceRecreate -and $compatibility.Compatible
if ($environmentReused) {
    "Reusing healthy environment with matching compose config hashes." |
        Set-Content -LiteralPath $composePreflight -Encoding UTF8
}
else {
    $reason = if ($ForceRecreate) {
        "Explicit -ForceRecreate"
    }
    else {
        $compatibility.Reasons -join "; "
    }
    $servicesToRecreate = [System.Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    if ($ForceRecreate) {
        foreach ($service in @("gaussdb-proxy", "tei", "ragflow-cpu")) {
            $null = $servicesToRecreate.Add($service)
        }
    }
    else {
        foreach ($compatibilityReason in $compatibility.Reasons) {
            if ($compatibilityReason -like "gaussdb-proxy *") {
                $null = $servicesToRecreate.Add("gaussdb-proxy")
            }
            elseif ($compatibilityReason -like "tei *") {
                $null = $servicesToRecreate.Add("tei")
            }
            else {
                $null = $servicesToRecreate.Add("ragflow-cpu")
            }
        }
    }
    $services = @($servicesToRecreate)
    "Recreating services [$($services -join ', ')]: $reason" |
        Set-Content -LiteralPath $composePreflight -Encoding UTF8
    $ErrorActionPreference = "Continue"
    & docker compose -f $composeFile up -d --no-build --no-deps --force-recreate @services 1>> $composePreflight 2>&1
    if ($LASTEXITCODE -ne 0) {
        Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
        exit $LASTEXITCODE
    }
}
$serviceDeadline = (Get-Date).AddMinutes(20)
$serviceHealthy = $false
do {
    Start-Sleep -Seconds 5
    try {
        $serviceHealth = Invoke-RestMethod -Uri "$($env:RAGFLOW_BASE_URL)/api/v1/system/healthz" -TimeoutSec 5
        $serviceHealthy = $serviceHealth.status -eq "ok"
    }
    catch {
        $serviceHealthy = $false
    }
} until ($serviceHealthy -or (Get-Date) -ge $serviceDeadline)
if (-not $serviceHealthy) {
    "RAGFlow did not become healthy" | Add-Content -LiteralPath $composePreflight -Encoding UTF8
    & docker compose -f $composeFile ps 1>> $composePreflight 2>&1
    & docker compose -f $composeFile logs --tail 500 ragflow-cpu 1>> $composePreflight 2>&1
    Set-Content -LiteralPath $exitCodeFile -Value 1 -Encoding ascii
    exit 1
}

$ErrorActionPreference = "Continue"
& docker exec -e GPU_PROOF_BASE_URL=http://tei:80 gaussdb-e2e-dual-ragflow python /e2e-code/docker/gpu-embedding-proof.py 1>> $cudaPreflight 2>&1
if ($LASTEXITCODE -ne 0) {
    Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
    exit $LASTEXITCODE
}
$containerTeiProof = $true
$ragflowRuntime = Get-ContainerEnvironment -Container "gaussdb-e2e-dual-ragflow"
$runtimeData = [ordered]@{
    variant = $ragflowRuntime.Values["GAUSSDB_VARIANT"]
    doc_engine = $ragflowRuntime.Values["DOC_ENGINE"]
    metadata_database = $ragflowRuntime.Values["DB_TYPE"]
    metadata_schema = $ragflowRuntime.Values["GAUSSDB_METADATA_SCHEMA"]
    embedding_batch_size = [int]$ragflowRuntime.Values["EMBEDDING_BATCH_SIZE"]
    feedback_enabled = $ragflowRuntime.Values["CHUNK_FEEDBACK_ENABLED"]
    source_revision = $ragflowRuntime.Values["GAUSSDB_E2E_SOURCE_REVISION"]
    tei_model = $ragflowRuntime.Values["TEI_MODEL"]
    ragflow_image = $ragflowRuntime.Image
    compose_config_hash = $ragflowRuntime.ConfigHash
    cuda_provider = $cudaHealth.provider
    cuda_model = $cudaHealth.model
    cuda_dimensions = [int]$cudaHealth.dimensions
    cuda_device = $cudaHealth.device
    container_tei_proof = $containerTeiProof
    environment_reused = $environmentReused
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
}
if (
    $runtimeData.variant -ne $Variant -or
    $runtimeData.doc_engine -ne "gaussdb" -or
    $runtimeData.metadata_database -ne "gaussdb" -or
    $runtimeData.metadata_schema -ne $env:GAUSSDB_E2E_METADATA_SCHEMA -or
    [string]$runtimeData.embedding_batch_size -ne $embeddingBatchSize -or
    $runtimeData.feedback_enabled -ne $env:CHUNK_FEEDBACK_ENABLED -or
    $runtimeData.source_revision -ne $env:GAUSSDB_E2E_SOURCE_REVISION -or
    $runtimeData.tei_model -ne $env:TEI_MODEL
) {
    Write-Utf8NoBom -Path $runtimeProfile -Content ($runtimeData | ConvertTo-Json -Depth 4)
    Set-Content -LiteralPath $exitCodeFile -Value 1 -Encoding ascii
    exit 1
}
Write-Utf8NoBom -Path $runtimeProfile -Content ($runtimeData | ConvertTo-Json -Depth 4)
$env:GAUSSDB_E2E_RUNTIME_PROFILE_PATH = $runtimeProfile

if ($NodeList -match 'test_tc_e2e_(406|408|501|502|503|504|801|802)_') {
    $ErrorActionPreference = "Continue"
    & $python (Join-Path $worktree "docker\gaussdb-e2e-model-preflight.py") 1> $modelPreflight 2>&1
    if ($LASTEXITCODE -ne 0) {
        Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
        exit $LASTEXITCODE
    }
}

$ErrorActionPreference = "Continue"
& $python (Join-Path $worktree "docker\seed-gaussdb-e2e-account.py") 1>> $stdout 2>> $stderr
if ($LASTEXITCODE -ne 0) {
    Set-Content -LiteralPath $exitCodeFile -Value $LASTEXITCODE -Encoding ascii
    exit $LASTEXITCODE
}

$ErrorActionPreference = "Continue"
$pytestArgs = @("-m", "pytest", "-vv", "-s") + $nodes + @("--junitxml=$junit")
try {
    $pytestProcess = Start-Process `
        -FilePath $python `
        -ArgumentList $pytestArgs `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr
}
catch {
    $_.Exception.Message | Set-Content -LiteralPath $watchdogLog -Encoding UTF8
    Set-Content -LiteralPath $exitCodeFile -Value 1 -Encoding ascii
    exit 1
}
$null = $pytestProcess.Handle
$finished = $pytestProcess.WaitForExit($suiteTimeoutSeconds * 1000)
if (-not $finished) {
    "pytest exceeded the $suiteTimeoutSeconds second anti-hang watchdog; this is not a product performance assertion." |
        Set-Content -LiteralPath $watchdogLog -Encoding UTF8
    & docker compose -f $composeFile ps 1>> $watchdogLog 2>&1
    & docker compose -f $composeFile logs --tail 500 ragflow-cpu 1>> $watchdogLog 2>&1
    & taskkill /PID $pytestProcess.Id /T /F 1>> $watchdogLog 2>&1
    Set-Content -LiteralPath $exitCodeFile -Value 124 -Encoding ascii
    exit 124
}
$pytestProcess.WaitForExit()
$code = $pytestProcess.ExitCode
Set-Content -LiteralPath $exitCodeFile -Value $code -Encoding ascii
exit $code
