# Logs in to auth-service and sends test orders to order-service.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\send-orders.ps1 -Count 30 -DelayMs 500
param(
    [int]$Count = 20,
    [int]$DelayMs = 500,
    [string]$Username = 'cashier',
    [string]$Password = 'cashier_pass'
)

$login = @{ username = $Username; password = $Password } | ConvertTo-Json
$token = (Invoke-RestMethod -Uri 'http://localhost:8002/login' -Method Post -ContentType 'application/json' -Body $login).access_token
$headers = @{ Authorization = "Bearer $token" }

$drinks = 'espresso', 'americano', 'cappuccino', 'latte', 'tea'

for ($i = 1; $i -le $Count; $i++) {
    $body = @{
        drink    = Get-Random -InputObject $drinks
        quantity = Get-Random -Minimum 1 -Maximum 4
        customer = "client-$i"
    } | ConvertTo-Json

    $response = Invoke-RestMethod -Uri 'http://localhost:8000/orders' -Method Post -ContentType 'application/json' -Headers $headers -Body $body
    Write-Host "$i/$Count accepted: $($response.order_id)"
    Start-Sleep -Milliseconds $DelayMs
}
