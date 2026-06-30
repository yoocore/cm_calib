import cv2, numpy as np

sim = cv2.imread('C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/calibration/VehSensor_0/rounds_20260630_165831/round_01/campaign/explore/start_00/initial.png')
score = cv2.imread('C:/CM_Projects/TM15.1_StreamaxCamera/SimOutput/calibration/VehSensor_0/rounds_20260630_165831/round_01/campaign/explore/start_00/initial_score.png')

left = score[:, :960]

# diff analysis
diff = cv2.absdiff(sim, left).max(axis=2)
ys, xs = np.where(diff > 0)
if len(ys):
    print(f'Annotated region: X={xs.min()}-{xs.max()} Y={ys.min()}-{ys.max()} Area={xs.max()-xs.min()+1}x{ys.max()-ys.min()+1}')

    # Show pixel colors in the annotated region
    for y in sorted(set(ys)):
        row_pixels = left[y, xs.min():xs.max()+1]
        colored = row_pixels[(row_pixels.max(axis=1) - row_pixels.min(axis=1)) > 20]
        if len(colored):
            unique = np.unique(colored[:10], axis=0)
            print(f'  Y={y}: unique BGR={unique[:3]}')

# Check panel text (score values)
right = score[:, 960:]
# Find text: look for non-white, non-gray pixels
panel_gray = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
text_mask = panel_gray < 200
text_y, text_x = np.where(text_mask)
if len(text_y):
    y_min, y_max = text_y.min(), text_y.max()
    print(f'\nPanel text: Y={y_min}-{y_max}')
    # Get lines
    for y in range(y_min, min(y_max+1, y_min+500, right.shape[0])):
        row = right[y, :, :]
        non_white = row[row.max(axis=1) < 200]
        if len(non_white):
            pass  # too verbose
