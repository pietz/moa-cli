# moa README demo - ask mode (council). Staged playback (demo/ask.py).
#   vhs demo/ask.tape
Output demo/ask.gif
Require uv

Set Shell "zsh"
Set FontFamily "Menlo"
Set FontSize 16
Set Width 1000
Set Height 320
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 30ms

# Off-screen: shadow `moa` with the staged player and warm uv so there's no stall.
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
Type `moa ask "Name one underrated CLI tool every dev should know. One sentence."`
Sleep 600ms
Enter

Sleep 8s
Sleep 2500ms
