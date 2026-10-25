#!/bin/sh
# MSSB_SHOT_CMD for the self-test: save the window as /shots/<name>.png
win=$(xdotool search --onlyvisible --name "MSSB Clone Maker" | head -1)
xdotool windowmove "$win" 0 0
[ "$1" = hover ] && xdotool mousemove 300 300
sleep 0.3
eval "$(xdotool getwindowgeometry --shell "$win")"
import -window root -crop "${WIDTH}x${HEIGHT}+0+0" +repage "/shots/$1.png"
