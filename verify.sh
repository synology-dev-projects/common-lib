# Inside verify.sh

# Get the absolute path of the directory where verify.sh sits
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_PATH="$SCRIPT_DIR/.venv"

if [ -d "$VENV_PATH" ]; then
    echo "⚙️ Found local .venv at $VENV_PATH... Activating."
    source "$VENV_PATH/bin/activate"
    # Double check we are using the right one
    echo "🐍 Python Location: $(which python)"
else
    echo "❌ CRITICAL: Local .venv not found in $SCRIPT_DIR"
    exit 1
fi

export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"
echo "📂 PYTHONPATH set to: $PYTHONPATH"

# Run pytest using the 'python -m' syntax
python -m pytest -v tests/