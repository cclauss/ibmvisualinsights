import time
import sys
import os
import threading
import _thread
from configparser import SafeConfigParser
import re
from os import path
import glob
import requests
import json
import random
import cv2

default_config = 'VICaptureCloud.config'

# Palette used by detection annotation
color_palette = [
    (255,0,0), (0,255,0), (0,0,255), (255,255,0), (255,0,255), (0,255,255),
    (128,0,0), (0,128,0), (0,0,128), (128,128,0), (128,0,128), (0,128,128),
    (64,0,0), (0,64,0), (0,0,64), (64,64,0), (64,0,64), (0,64,64),
    (192,0,0), (0,192,0), (0,0,192), (192,192,0), (192,0,192), (0,192,192)
    ]

# Score image on cloud
def cloud_score_image(image_path, config, score_type):

    # Pick up options
    url = config['Cloud']['URL']
    if score_type == 'cls':
        cell = config['Cloud']['CLSCell']
        product = config['Cloud']['CLSProduct']
    else:
        cell = config['Cloud']['ODCell']
        product = config['Cloud']['ODProduct']

    # Open file and load into content
    data = open(image_path, 'rb').read()

    # Request parameters
    params = {'productType': product, 'cell': cell,
        'user': os.environ['VIUSER'], 'tenant': config['Cloud']['Tenant'],
        'solution': 'vi'}

    # Request headers
    headers = {'user': os.environ['VIUSER'], 'APIKey':os.environ['VIAPIKEY'], 'Content-Type': 'application/binary' }

    # score against the Cloud Scoring API
    try:
        r = requests.post(url, params=params, headers=headers, data=data)
    except requests.exceptions.RequestException as e:
        print(e)

    return r

# Parse edge scoring response object (classification)
def parseResponse(text):

    # Set best detection and confidence
    best_det = None
    best_conf = 0.0

    try:
        # Parse the response
        parsed = json.loads(text)

        # Iterate through the detections, finding the one with the highest confidence
        dets = parsed['detections']
        if dets != None:
            if len(dets) == 1:
                prob_types = dets[0]['probableTypes']
                for det in prob_types:
                    det_type = det['type']
                    det_conf = float(det['confidence'])
                    if det_conf > best_conf:
                        best_det = det_type
                        best_conf = det_conf

    except ValueError:  # includes simplejson.decoder.JSONDecodeError
        # If text can't be parsed then print text to console
        print("Response Error: {}", text)

    return (best_det, best_conf)

# Parse the response from the OD model
def parseODResponse(text):

    dets = None
    try:
        # Parse and return the detections object
        parsed = json.loads(text)
        dets = parsed['detections']

    except ValueError:  # includes simplejson.decoder.JSONDecodeError
        # If text can't be parsed then print text to console
        print("Response Error: {}", text)

    return dets

# Draws the text in the window
def showText(img, text, x, y, color):
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, text, (x, y), font, 0.6, color, 2, cv2.LINE_AA)

def showDuration(frame, duration):
    if duration > 0.0:
        dur_ms = int(duration * 1000)
        fps = int(1 / duration)
        showText(frame, "{:4d} ms, {:4d} FPS".format(dur_ms, fps), 445, 465, (0, 255, 0))

# Draws the classification result
def showClass(frame, det, conf):
    showText(frame, "{} ({:2.1f}%)".format(det, conf), 5, 25, (0, 255, 0))

# Draws a detection rectangle
def drawRect(img, xmin, ymin, xmax, ymax, color, thickness):
    cv2.rectangle(img, (xmin, ymin), (xmax, ymax), color, thickness)

# Draws a line for the key
def drawKeyLine(img, y, width, color):
    cv2.line(img, (10, y-10), (10+width, y-10), color, 2)

# globals
max_color_num = 0
colors = {}
max_pos = 80
positions = {}

def getColor(det):
    global max_color_num, colors
    if det in colors:
        return colors[det]
    else:
        color = color_palette[max_color_num]
        colors[det] = color
        max_color_num += 1
        return color

def getPosition(det):
    global max_pos, positions
    if det in positions:
        return positions[det]
    else:
        pos = max_pos
        positions[det] = pos
        max_pos += 20
        return pos


# Shows the detections
def showDets(frame, cropped_frame, dets):
    global colors

    for d in dets:
        # Get det and confidence
        probableTypes = d['probableTypes']
        det = probableTypes[0]['type']
        conf = probableTypes[0]['confidence']

        if det != '':
            # get positions
            position = d['position']
            x = position['x']
            y = position['y']
            w = position['width']
            h = position['height']

            color = getColor(det)

            # Draw the detection rectangle
            drawRect(cropped_frame, x, y, x+w, y+h, color, int((conf / 100) * 4) + 1)

    # Draw the keys
    for det, color in colors.items():
        y = getPosition(det)
        drawKeyLine(frame, y, 10, color)
        showText(frame, "{}".format(det), 25, y, color)

# Records video and scores it
def recordVideo(config):

    # Get options
    image_path = config['Record']['OutputDirectory']

    # Crop
    crop = config['Crop'].getboolean('crop')
    xmin = config['Crop']['xmin']
    ymin = config['Crop']['ymin']
    xmax = config['Crop']['xmax']
    ymax = config['Crop']['ymax']

    score_type = 'cls'
    score = False

    # Set up camera and windows
    cam_id = 0
    cam = cv2.VideoCapture(cam_id)
    cv2.namedWindow("capture", cv2.WINDOW_NORMAL)
    if crop:
        cv2.namedWindow("crop")

    # Set up timers
    last_time = time.clock()
    duration = 0
    det = None
    conf = 0.0
    dets = None

    # Repeat until duration finishes
    while True:

        # Record a frame
        ret, frame = cam.read()
        if not ret:
            break

        # Define the crop frame
        if crop:
            crop_frame = frame[xmin:xmax, ymin:ymax]
            score_frame = crop_frame.copy()
        else:
            crop_frame = frame
            score_frame = frame.copy()

        score_window = False

        # Get keyboard input
        k = cv2.waitKey(1)
        action = False

        if k%256 == 27:
            # ESC pressed - break from loop
            print("Escape hit, closing...")
            break
        elif k%256 == 32:
            # Space pressed - take action
            action = True
        elif k%256 == 115 or k%256 == 83:
            # S pressed - toggle scoring
            score = not score
        elif k%256 == 99 or k%256 == 67:
            # C pressed - switch to cls mode
            score_type = 'cls'
        elif k%256 == 111 or k%256 == 79:
            # O pressed - switch to od mode
            score_type = 'od'
        elif k%256 >= 48 and k%256 <= 57:
            # (0-9) pressed - switch camera ID if changed
            if (k%256 - 48) != cam_id:
                cam_id = k%256 - 48
                cam = cv2.VideoCapture(cam_id)


        # Check action flag - if set then write the image
        if action:

            # Create image path Name
            image_basename = path.join(image_path, "{}".format(time.time()))
            image_filename = image_basename + ".jpg"

            # Write image to file
            cv2.imwrite(image_filename, score_frame)

            # If scoring enabled
            if score:

                # Set dets to None for the iteration
                dets = None
                det = None

                r = cloud_score_image(image_filename, config, score_type)

                if r.status_code == 200:

                    if score_type == 'cls':
                        # Get det and conf from classifier
                        (det, conf) = parseResponse(r.text)
                    else:
                        # Get dets from OD response
                        dets = parseODResponse(r.text)

                    # Create the score window if it doesn't exist
                    if not score_window:
                        score_window = cv2.namedWindow("score")

                    # Get a snapshot of the frame
                    snapshot = score_frame.copy()

                    # Show the class or draw the detections on the snapshot
                    if det:
                        showClass(snapshot, det, conf)
                    if dets:
                        showDets(snapshot, snapshot, dets)

                    cv2.imshow("score", snapshot)

                else:
                    # Score failed
                    print("{}: {}".format(r.status_code, r.text))

                # Remove file if scoring
                os.remove(image_filename)

        # Calc timings
        current_time = time.clock()
        duration = current_time - last_time
        last_time = current_time

        # Show timer / frame rate
        showDuration(frame, duration)

        # Show the frames in a window
        cv2.imshow("capture", frame)
        if crop:
            cv2.imshow("crop", crop_frame)


    # Stop capture and delete windows
    cam.release()
    cv2.destroyAllWindows()

def printHelp():
    print('Commands:')
    print('Esc: Quit')
    print('Space: Action (save image or score image)')
    print('S: Toggle scoring')
    print('C: Switch to classification mode (default)')
    print('O: Switch to Object Detection mode')
    print('(0-9): Switch to camera ID #0-9 (default 0)')

if __name__ == '__main__':
    args = sys.argv[1:]

    # Parse config file
    parser = SafeConfigParser()
    config_path = args[0] if args else default_config
    config = parser.read(config_path)

    # Print help text
    printHelp()

    # Start recording
    recordVideo(parser)