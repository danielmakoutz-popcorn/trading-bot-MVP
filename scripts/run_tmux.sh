#!/usr/bin/env bash
set -e
SESSION="trading-bot"
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session $SESSION already exists. Attaching..."
  tmux attach -t "$SESSION"
  exit 0
fi

tmux new-session -d -s "$SESSION"
tmux rename-window -t "$SESSION:0" 'bot'
tmux send-keys -t "$SESSION:0" 'source .venv/bin/activate || true' C-m
tmux send-keys -t "$SESSION:0" 'python main.py' C-m
echo "Started trading bot in tmux session '$SESSION'. Attach with: tmux attach -t $SESSION"
