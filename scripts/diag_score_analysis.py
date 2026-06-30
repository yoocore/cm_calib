"""Analyze the score image — extract boxes and overlay info."""
import json, sys
from pathlib import Path
import cv2
import numpy as np

score_path = "C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/calibration/VehSensor_0/rounds_20260630_165831/round_01/campaign/explore/start_00/initial_score.png"
sim_path = "C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/calibration/VehSensor_0/rounds_20260630_165831/round_01/campaign/explore/start_00/initial.png"
result_path = "C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/calibration/VehSensor_0/rounds_20260630_165831/round_01/campaign/explore/start_00/result.json"

with open(result_path) as f:
    result = json.load(f)

score_img = cv2.imread(score_path)
sim_img = cv2.imread(sim_path)

print(f"Score image: {score_img.shape}")
print(f"Sim image: {sim_img.shape}")

# The score image is sim image (left) + panel (right)
sim_h, sim_w = sim_img.shape[:2]
left_part = score_img[:, :sim_w]
right_part = score_img[:, sim_w:]

# Save right panel for analysis
cv2.imwrite("C:/Users/yooco/Desktop/score_right_panel.jpg", right_part, [cv2.IMWRITE_JPEG_QUALITY, 90])

# Find non-gray pixels in the left part to detect drawn boxes
# Boxes are drawn in color (not gray)
hsv = cv2.cvtColor(left_part, cv2.COLOR_BGR2HSV)
# Find colored pixels (not black/white/gray)
saturation = hsv[:, :, 1]
colored_mask = saturation > 30

# Find bounding boxes of colored regions
colored_y, colored_x = np.where(colored_mask)
if len(colored_y) > 0:
    print(f"\nColored annotation region in sim image:")
    print(f"  X range: {colored_x.min()} to {colored_x.max()}")
    print(f"  Y range: {colored_y.min()} to {colored_y.max()}")

# Look for rectangles (lines) in the image
gray_left = cv2.cvtColor(left_part, cv2.COLOR_BGR2GRAY)
edges = cv2.Canny(gray_left, 50, 150)
lines = cv2.HoughLinesP(edges, 1, np.pi/180, 100, minLineLength=50, maxLineGap=10)
if lines is not None:
    print(f"\nDetected {len(lines)} line segments")

    # Group lines into rectangles
    h_lines = []  # horizontal
    v_lines = []  # vertical
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(y2 - y1) < 5:  # horizontal
            h_lines.append((min(x1,x2), y1, max(x1,x2), y2))
        elif abs(x2 - x1) < 5:  # vertical
            v_lines.append((x1, min(y1,y2), x2, max(y1,y2)))

    print(f"  {len(h_lines)} horizontal lines, {len(v_lines)} vertical lines")
    if h_lines:
        ys = sorted(set(y for _,y,_,_ in h_lines))
        print(f"  Y positions of horizontal lines: {ys[:10]}{'...' if len(ys)>10 else ''}")
    if v_lines:
        xs = sorted(set(x for x,_,_,_ in v_lines))
        print(f"  X positions of vertical lines: {xs[:10]}{'...' if len(xs)>10 else ''}")

# Also check the score image panel text
# Try to find text region
print(f"\nRight panel first 10 rows pixel values (center column):")
panel_mid = sim_w + score_img.shape[1]//2 - sim_w//2
for y in range(0, min(200, right_part.shape[0])):
    pixel = right_part[y, right_part.shape[1]//2]
    if np.any(pixel < 200):  # non-white
        print(f"  y={y}: BGR={pixel}")

# Check where the hull outline is — search for the blue-ish color from the annotation
# The palette first color is (70, 80, 230) in BGR = (230, 80, 70)
# Let's find pixels close to this color
target_color = np.array([230, 80, 70])  # BGR
diff = np.abs(left_part.astype(int) - target_color).max(axis=2)
mask = diff < 40
blue_pixels = np.where(mask)
if len(blue_pixels[0]) > 0:
    print(f"\nAnnotation-colored pixels (blue palette):")
    print(f"  Count: {len(blue_pixels[0])}")
    ys, xs = blue_pixels
    print(f"  Y: {ys.min()}-{ys.max()}, X: {xs.min()}-{xs.max()}")
    print(f"  Center of mass: X={xs.mean():.0f}, Y={ys.mean():.0f}")
    # Draw bounding box around these on sim image
    viz = left_part.copy()
    cv2.rectangle(viz, (xs.min(), ys.min()), (xs.max(), ys.max()), (0, 255, 0), 2)
    cv2.putText(viz, f"Box: ({xs.min()},{ys.min()}) to ({xs.max()},{ys.max()})", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.imwrite("C:/Users/yooco/Desktop/score_box_analysis.jpg", viz, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"  Saved analysis to Desktop/score_box_analysis.jpg")
else:
    print("\nNo annotation-colored pixels found!")
    # Try finding any non-gray, non-white pixel
    gray_mask = (np.abs(left_part.astype(int) - 128).max(axis=2) < 30)
    non_gray = ~gray_mask & (left_part.max(axis=2) < 250)
    ng_y, ng_x = np.where(non_gray)
    if len(ng_y) > 0:
        print(f"  Non-gray pixels: {len(ng_y)}")
        print(f"  Y: {ng_y.min()}-{ng_y.max()}, X: {ng_x.min()}-{ng_x.max()}")
