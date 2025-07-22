#!/bin/bash
echo "Byter namn på alla filer i ./Eqiupment till *.png..."
cd Equipment
for file in *; do
  if [[ ! "$file" == *.* ]]; then
    mv "$file" "$file.png"
    echo "Bytte namn: $file -> $file.png"
  fi
done
echo "Klar!"