# moa README demo - distill mode (synthesis). Staged playback (demo/distill.py).
#   vhs demo/distill.tape
Output demo/distill.gif
Require uv

Set Shell "zsh"
Set FontFamily "Menlo"
Set FontSize 16
Set Width 1000
Set Height 320
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
Type `moa distill "What are the top 5 principles of good API design?"`
Sleep 600ms
Enter

Sleep 12s
Sleep 2500ms
