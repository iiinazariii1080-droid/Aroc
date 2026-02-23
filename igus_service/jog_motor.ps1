# Подвигать мотором: короткий jog вперёд и назад.
# URL сервиса (API), не мотора: сервис сам подключается к мотору по IGUS_MOTOR_IP:501.
# Задать свой URL: .\jog_motor.ps1 http://хост:порт   или  $env:IGUS_SERVICE_URL="http://..."; .\jog_motor.ps1
$base = if ($args[0]) { $args[0].TrimEnd("/") } elseif ($env:IGUS_SERVICE_URL) { $env:IGUS_SERVICE_URL.TrimEnd("/") } elseif ($env:SERVICE_URL) { $env:SERVICE_URL.TrimEnd("/") } else { "http://127.0.0.1:8101" }
$speed = 800
Write-Host "Service URL: $base"

try {
    $ready = Invoke-RestMethod -Uri "$base/ready" -Method Get -ErrorAction Stop
    if ($ready.status -ne "ready") {
        Write-Host "Drive not ready:" $ready
        exit 1
    }
} catch {
    Write-Host "Service at $base not reachable. Start it first: python main.py (or docker-compose up)."
    Write-Host "Motor IP is set in app/docker-compose (IGUS_MOTOR_IP:501), not here."
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host "Jog + (1 sec)..."
Invoke-RestMethod -Uri "$base/api/v1/drive/jog_start" -Method Post -ContentType "application/json" -Body (@{ direction = "positive"; speed = $speed; ttl_ms = 200 } | ConvertTo-Json) -ErrorAction Stop | Out-Null
Start-Sleep -Seconds 1
Invoke-RestMethod -Uri "$base/api/v1/drive/jog_stop" -Method Post -ContentType "application/json" -Body "{}" -ErrorAction Stop | Out-Null

Write-Host "Jog - (1 sec)..."
Invoke-RestMethod -Uri "$base/api/v1/drive/jog_start" -Method Post -ContentType "application/json" -Body (@{ direction = "negative"; speed = $speed; ttl_ms = 200 } | ConvertTo-Json) -ErrorAction Stop | Out-Null
Start-Sleep -Seconds 1
Invoke-RestMethod -Uri "$base/api/v1/drive/jog_stop" -Method Post -ContentType "application/json" -Body "{}" -ErrorAction Stop | Out-Null

Write-Host "Done."
