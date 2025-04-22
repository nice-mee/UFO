import base64
import re
import time
import random
from io import BytesIO
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from PIL import Image

from ufo.llm.base import BaseService
from ufo.utils import print_with_color


class GeminiService(BaseService):
    """
    A service class for Gemini models.
    """

    def __init__(self, config: Dict[str, Any], agent_type: str):
        """
        Initialize the Gemini service.
        :param config: The configuration.
        :param agent_type: The agent type.
        """
        self.config_llm = config[agent_type]
        self.config = config
        self.model = self.config_llm["API_MODEL"]
        self.prices = self.config["PRICES"]
        self.max_retry = self.config["MAX_RETRY"]
        self.api_type = self.config_llm["API_TYPE"].lower()
        self.client = genai.Client(
            api_key=self.config_llm["API_KEY"],
        )

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        n: int = 1,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Generates completions for a given list of messages.
        :param messages: The list of messages to generate completions for.
        :param n: The number of completions to generate for each message.
        :param temperature: Controls the randomness of the generated completions. Higher values (e.g., 0.8) make the completions more random, while lower values (e.g., 0.2) make the completions more focused and deterministic. If not provided, the default value from the model configuration will be used.
        :param max_tokens: The maximum number of tokens in the generated completions. If not provided, the default value from the model configuration will be used.
        :param top_p: Controls the diversity of the generated completions. Higher values (e.g., 0.8) make the completions more diverse, while lower values (e.g., 0.2) make the completions more focused. If not provided, the default value from the model configuration will be used.
        :param kwargs: Additional keyword arguments to be passed to the underlying completion method.
        :return: A list of generated completions for each message and the estimated cost.
        """

        temperature = (
            temperature if temperature is not None else self.config["TEMPERATURE"]
        )
        top_p = top_p if top_p is not None else self.config["TOP_P"]
        max_tokens = max_tokens if max_tokens is not None else self.config["MAX_TOKENS"]
        genai_config = types.GenerateContentConfig(
            candidate_count=n,
            max_output_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )

        responses = []
        cost = 0.0
        processed_messages = self.process_messages(messages)

        # Default parameters from OpenAI
        # Ref: _calculate_retry_timeout from https://github.com/openai/openai-python/blob/main/src/openai/_base_client.pys
        initial_delay = 0.5
        max_delay = 8.0
        jitter_factor = 0.25

        for _ in range(n):
            for attempt in range(self.max_retry):
                try:
                    response = self.client.models.generate_content(
                        model=self.model,
                        contents=processed_messages,
                        config=genai_config,
                    )
                    responses.append(response.text)
                    prompt_tokens = response.usage_metadata.prompt_token_count
                    completion_tokens = response.usage_metadata.candidates_token_count
                    cost += self.get_cost_estimator(
                        self.api_type,
                        self.model,
                        self.prices,
                        prompt_tokens,
                        completion_tokens,
                    )
                    break
                except Exception as e:
                    # Calculate backoff with jitter
                    delay = min(initial_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(-jitter_factor * delay, 0)
                    sleep_time = delay + jitter
                    print_with_color(
                        f"Error during Gemini API request, attempt {attempt+1}/{self.max_retry}: {e}. "
                        f"Retrying in {sleep_time:.2f}s...", 
                        "yellow"
                    )
                    time.sleep(sleep_time)

        if responses[0] == None:
            print("Warn: Gemini API response is None, please check the API key and model.")

        return responses, cost

    def process_messages(self, messages: List[Dict[str, str]]) -> List[str]:
        """
        Process the given messages and extract prompts from them.
        :param messages: The messages to process.
        :return: A list of prompts extracted from the messages.
        """

        prompt_contents = []

        if isinstance(messages, dict):
            messages = [messages]
        for message in messages:
            if message["role"] == "system":
                prompt = f"Your general instruction: {message['content']}"
                prompt_contents.append(prompt)
            else:
                for content in message["content"]:
                    if content["type"] == "text":
                        prompt = content["text"]
                        prompt_contents.append(prompt)
                    elif content["type"] == "image_url":
                        prompt = self.base64_to_image(content["image_url"]["url"])
                        prompt_contents.append(prompt)
        return prompt_contents

    def base64_to_image(self, base64_str: str) -> Image.Image:
        """
        Converts a base64 encoded image string to a PIL Image object.
        :param base64_str: The base64 encoded image string.
        :return: The PIL Image object.
        """

        base64_data = re.sub("^data:image/.+;base64,", "", base64_str)
        byte_data = base64.b64decode(base64_data)
        image_data = BytesIO(byte_data)
        img = Image.open(image_data)
        return img
