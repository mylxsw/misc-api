#!/bin/bash

# Create dummy images
python -c "from PIL import Image; Image.new('RGB', (100, 100), 'red').save('img1.png')"
python -c "from PIL import Image; Image.new('RGB', (100, 100), 'blue').save('img2.png')"

# Create python script to generate json file (safer than shell for json construction)
cat <<EOF > create_payload.py
import json
import base64

with open("img1.png", "rb") as f:
    b1 = base64.b64encode(f.read()).decode('utf-8')
with open("img2.png", "rb") as f:
    b2 = base64.b64encode(f.read()).decode('utf-8')

payload = {
    "images": [b1, b2],
    "direction": "horizontal"
}

with open("payload.json", "w") as f:
    json.dump(payload, f)
EOF

# Run generation
uv run python create_payload.py

# Send request
echo "Sending request using @payload.json..."
curl -X POST http://localhost:8000/v1/image/stitch \
  -H "Content-Type: application/json" \
  -d @payload.json \
  | jq -r .image_b64 | base64 -d > result_file.png

if [ -f "result_file.png" ]; then
    echo "Success: Created result_file.png"
    rm payload.json create_payload.py result_file.png
else
    echo "Failed"
fi
