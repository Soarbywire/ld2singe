# Changelog

## 1.0

First release.

- Matches a Singe game's video against the laserdisc video from a MAME CHD, scene by scene, by picture
  and by movement.
- Detects whether the game's video is flipped, mirrored or rotated, and how it is scaled and shifted.
- Writes one new `.m2v` and `.ogg` with the game's original frame numbers, so the game's scripts are unchanged.
- Starts every laserdisc stretch on the exact frame, by comparing thumbnails with the scan.
- Places the picture where the game's video had it (`--framing game`) or full size (`--framing disc`).
- Fills short unmatched gaps inside a clip from the laserdisc (`--bridge`).
- Matches the laserdisc's sound level to the game's own sound.
- Check sheets and `report.csv` for reviewing the matches before building.
