import json
import re
from google import genai
from google.genai import types
from PIL import Image


def extract_weapon_data_with_gemma(image_path):
    # 1. Initialize the Google GenAI client
    client = genai.Client()

    # 2. Load the screenshot
    try:
        image = Image.open(image_path)
    except FileNotFoundError:
        print(f"Error: Could not find the image at {image_path}")
        return

    # 3. Define the prompt (Added explicit markdown instructions)
    prompt = """
    Analyze this game inventory screenshot.
    1. Extract the weapon name (the very first line of text at the top left).
    2. Extract the crafted stats at the bottom (the green text showing percentage modifiers).
    
    Return the data as a JSON object inside a markdown code block exactly like this:
    ```json
    {
      "weapon_name": "Name of the weapon",
      "crafted_stats": {
        "Stat Name": "Value %"
      }
    }
    ```
    """

    print(f"Sending '{image_path}' to Google AI Studio (Gemma 3)...")

    # 4. Call the hosted Gemma 3 model (Removed the JSON MIME type config)
    response = client.models.generate_content(
        model='gemma-3-27b-it',
        contents=[image, prompt],
        config=types.GenerateContentConfig(
            temperature=0.0  # Keeps the OCR strictly factual
        )
    )

    raw_text = response.text

    # 5. Extract the JSON string using Regex
    # This looks for anything between ```json and ```
    json_match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)

    if json_match:
        json_string = json_match.group(1)
    else:
        # Fallback just in case the model didn't use the markdown wrappers
        json_string = raw_text.strip()

    # 6. Parse and display the results
    try:
        data = json.loads(json_string)

        print("\n--- EXTRACTION RESULTS ---")
        print(f"Weapon Name: {data.get('weapon_name', 'Not found')}")
        print("-" * 25)

        stats = data.get('crafted_stats', {})
        if stats:
            for stat, value in stats.items():
                print(f"{stat}: {value}")
        else:
            print("No crafted stats found.")

    except json.JSONDecodeError:
        print("Failed to parse JSON. The model might have returned unexpected text:")
        print(raw_text)


if __name__ == "__main__":
    screenshot_path = "image.png"
    extract_weapon_data_with_gemma(screenshot_path)
