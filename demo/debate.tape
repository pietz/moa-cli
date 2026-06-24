# moa README demo - debate mode (adversarial + verdict). Staged playback (demo/debate.py).
#   vhs demo/debate.tape
Output demo/debate.gif
Require uv

Set Shell "zsh"
Set FontFamily "Menlo"
Set FontSize 16
Set Width 1000
Set Height 420
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

Hide
Type "cd /Users/pietz/Private/moa-cli"
Enter
Type "moa(){ uv run python demo/$1.py }"
Enter
Type "uv run python -c '1' >/dev/null 2>&1"
Enter
Type "clear"
Enter
Show

Sleep 800ms
Type `moa debate "Should we move our database from SQLite to Postgres now?"`
Sleep 600ms
Enter

Sleep 16s
Sleep 2500ms
