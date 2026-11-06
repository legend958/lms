param(
    [ValidateSet("up", "down", "migrate", "seed", "test", "lint", "fmt", "logs", "ps", "build")]
    [string]$Command = "up"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$composeArgs = @("-f", "infra/docker-compose.yml")
if (Test-Path (Join-Path $root ".env")) {
    $composeArgs = @("--env-file", ".env") + $composeArgs
}

Push-Location $root
try {
    switch ($Command) {
        "up" {
            docker compose @composeArgs up -d --build
            Write-Host "Web: http://localhost:3000  API: http://localhost:8000/docs"
        }
        "down" { docker compose @composeArgs down }
        "migrate" { docker compose @composeArgs exec api alembic upgrade head }
        "seed" { docker compose @composeArgs exec api python -m app.seed }
        "test" { docker compose @composeArgs exec api pytest -q }
        "lint" {
            docker compose @composeArgs exec api ruff check app tests
            docker compose @composeArgs exec api ruff format --check app tests
            docker compose @composeArgs exec api mypy app
            docker compose @composeArgs exec web npm run lint
        }
        "fmt" {
            docker compose @composeArgs exec api ruff format app tests
            docker compose @composeArgs exec api ruff check --fix app tests
        }
        "logs" { docker compose @composeArgs logs -f --tail=100 }
        "ps" { docker compose @composeArgs ps }
        "build" { docker compose @composeArgs build }
    }
}
finally {
    Pop-Location
}
