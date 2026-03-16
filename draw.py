import numpy as np
import cv2
from sketchpy import canvas
import os

def main():
    txt_file = "image_pixels.txt"
    output_image = "reconstructed_priya.jpg"

    if not os.path.exists(txt_file):
        print(f"Error: {txt_file} not found.")
        return

    print("Reading txt file...")
    # Loading as uint8 is much faster and saves memory
    data = np.loadtxt(txt_file, dtype=np.uint8)
    print(f"Loaded data shape: {data.shape}")

    # THE FIX: Based on your log, Height=1600, Width=1200
    height = 1600
    width = 1200
    channels = 3

    try:
        # Reshape to the exact original dimensions
        img_array = data.reshape(height, width, channels)

        # IMPORTANT: If your output looks 'Blue', it's because OpenCV uses BGR.
        # Most TXT exports use RGB. We swap them here:
        img_final = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

        # Save the single, corrected image
        cv2.imwrite(output_image, img_final)
        print(f"Image reconstructed perfectly as {output_image}")

        # Start the sketch
        print("Starting the drawing...")
        obj = canvas.sketch_from_image(output_image)
        obj.draw()

    except ValueError as e:
        print(f"Error during reshape: {e}")
        print("Double check if the TXT file contains exactly 1,920,000 rows.")

if __name__ == "__main__":
    main()