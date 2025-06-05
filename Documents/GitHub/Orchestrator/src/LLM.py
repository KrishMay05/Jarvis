import os
import google.generativeai as genai

# Initialize the Gemini client
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def query_llm(prompt: str, model: str = "gemini-2.0-flash"):
    """
    Send `prompt` to Google Gemini and return the response text.
    """
    print(f">>> Prompt:\n{prompt}\n")
    model = genai.GenerativeModel(model)
    response = model.generate_content(
        "You are a helpful AI assistant similar to Jarvis from Iron Man. "
        "You should format your responses with swagger and confidence, "
        "similar to Jarvis. Answer the following prompt concisely:\n\n"
        + prompt
    )
    final_response = response.text.strip()
    print(f"<<< Response:\n{final_response}\n")
    return final_response