# Exécute un job Spark du pipeline dans un conteneur Linux, sur le poste de
# développement.
#
# Pourquoi un conteneur plutôt que le venv directement : Spark ne sait pas
# écrire de fichiers sous Windows sans `winutils.exe`, un binaire Hadoop
# distribué de façon non officielle. Plutôt que d'installer un exécutable non
# signé, on exécute dans la même image que la VM — Linux, et surtout parité
# exacte avec la production (décision D-112).
#
# Usage :
#   .\scripts\executer-job.ps1 consolider_installments
#   .\scripts\executer-job.ps1 agreger_installments -Echantillon 100000
#
# Prérequis, une seule fois :
#   docker build -f docker/airflow/Dockerfile -t crediscore-spark:dev docker/

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Job,

    # 0 = jeu complet. Une valeur non nulle limite les lignes lues : développer
    # sur 100 000 lignes plutôt que 13,6 millions ramène l'itération de
    # plusieurs minutes à quelques secondes.
    [int]$Echantillon = 0,

    [int]$MemoireGo = 3
)

$ErrorActionPreference = "Stop"

$depot = Split-Path -Parent $PSScriptRoot
$projet = Split-Path -Parent $depot

$entree = Join-Path $projet "input"
$travail = Join-Path $projet "donnees_pipeline"

if (-not (Test-Path $entree)) {
    throw "Dossier des donnees introuvable : $entree"
}
if (-not (Test-Path $travail)) {
    New-Item -ItemType Directory -Path $travail | Out-Null
}

$image = "crediscore-spark:dev"
if (-not (docker image inspect $image 2>$null)) {
    Write-Host "Image $image absente. Construction (une seule fois, ~10 min)..."
    docker build -f (Join-Path $depot "docker\airflow\Dockerfile") -t $image (Join-Path $depot "docker")
}

Write-Host "Job        : $Job"
Write-Host "Echantillon: $(if ($Echantillon -eq 0) { 'jeu complet' } else { "$Echantillon lignes" })"
Write-Host ""

docker run --rm `
    -v "${depot}:/travail" `
    -v "${entree}:/donnees/input:ro" `
    -v "${travail}:/donnees/pipeline" `
    -e CREDISCORE_ENTREE=/donnees/input `
    -e CREDISCORE_TRAVAIL=/donnees/pipeline `
    -e CREDISCORE_ECHANTILLON=$Echantillon `
    -e CREDISCORE_MEMOIRE_GO=$MemoireGo `
    -w /travail `
    $image `
    python "pipelines/spark_jobs/$Job.py"

if ($LASTEXITCODE -ne 0) {
    throw "Le job $Job a echoue (code $LASTEXITCODE)."
}
