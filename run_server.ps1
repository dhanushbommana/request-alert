# Kill any process listening on port 5000, then start the Flask app
$port = 5000
try {
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $pid = $conn.OwningProcess
        Write-Host "Killing existing process on port $port (PID $pid)"
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
    }
} catch {
    Write-Host "No previous process found or insufficient privileges to query TCP connections."
}
Write-Host "Starting Flask app (python app.py)"
python app.py
