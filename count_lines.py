#!/bin/bash
for file in $1/*.py; do
    echo "$(wc -l < "$file") lines in $file"
done
