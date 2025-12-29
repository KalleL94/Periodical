#!/bin/bash
# Benchmark script för att mäta response times

echo "🚀 Performance Benchmark"
echo "========================"
echo ""

# Starta servern i bakgrunden om den inte körs
# (Du måste ha servern igång först)

# Funktion för att mäta tid
measure() {
    local url=$1
    local name=$2

    echo "Testing: $name"
    echo "URL: $url"

    # Kör 5 requests och ta median
    times=()
    for i in {1..5}; do
        time=$(curl -o /dev/null -s -w '%{time_total}\n' "$url")
        times+=($time)
        echo "  Run $i: ${time}s"
    done

    # Beräkna genomsnitt (enkel variant)
    total=0
    for t in "${times[@]}"; do
        total=$(echo "$total + $t" | bc)
    done
    avg=$(echo "scale=3; $total / 5" | bc)
    echo "  Average: ${avg}s"
    echo ""
}

echo "Förutsättning: Applikationen måste köra på http://localhost:8000"
echo "och du måste vara inloggad (cookies krävs)"
echo ""
read -p "Tryck Enter för att fortsätta..."
echo ""

# Testa olika endpoints
measure "http://localhost:8001/" "Dashboard (homepage)"
measure "http://localhost:8001/month/1?year=2025&month=1" "Month view (single person)"
measure "http://localhost:8001/month?year=2025&month=1" "Month view (all persons)"
measure "http://localhost:8001/year/1?year=2025" "Year view (single person)"

echo "✅ Benchmark complete!"
echo ""
echo "Förväntade resultat med optimisering:"
echo "  Dashboard: <0.5s"
echo "  Month (single): <0.3s"
echo "  Month (all): <1.5s"
echo "  Year: <2.0s"
