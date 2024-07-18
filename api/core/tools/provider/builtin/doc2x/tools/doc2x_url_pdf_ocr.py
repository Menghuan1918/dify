from core.tools.tool.builtin_tool import BuiltinTool  # noqa: I001
from core.tools.errors import ToolProviderCredentialValidationError
from core.tools.entities.tool_entities import ToolInvokeMessage
from typing import Any, Union
from httpx import post, get
import json
import time
import io


class Doc2xURLPDFOCRTool(BuiltinTool):
    def _invoke(
        self,
        user_id: str,
        tool_parameters: dict[str, Any],
    ) -> Union[ToolInvokeMessage, list[ToolInvokeMessage]]:
        api_key = self.runtime.credentials.get('doc2x_api_key', None)
        if not api_key:
            raise ToolProviderCredentialValidationError('Please input API key')

        input_url = tool_parameters.get('pdf_url', '')
        if input_url == '':
            raise Exception('Please input image URL')
        ocr = tool_parameters.get('correction', False)
        get_limit = tool_parameters.get('get_limit', 1)

        # Refresh API key if necessary
        if not api_key.startswith('sk-'):
            response = post(
                'https://api.doc2x.noedgeai.com/api/token/refresh',
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=30,
            )
            if response.status_code != 200:
                raise Exception(response.text)
            real_api_key = json.loads(response.content.decode('utf-8'))['data']['token']
        else:
            real_api_key = str(api_key)

        # Get limit of the API key if set
        if get_limit:
            if real_api_key.startswith('sk-'):
                url = 'https://api.doc2x.noedgeai.com/api/v1/limit'
            else:
                url = 'https://api.doc2x.noedgeai.com/api/platform/limit'
            response = get(url, headers={'Authorization': f'Bearer {real_api_key}'}, timeout=30)
            if response.status_code != 200:
                raise Exception(response.text)
            return self.create_text_message(str(response.json()['data']['remain']))

        ocr = 1 if ocr else 0

        if real_api_key.startswith('sk-'):
            url = 'https://api.doc2x.noedgeai.com/api/v1/async/pdf'
        else:
            url = 'https://api.doc2x.noedgeai.com/api/platform/async/pdf'

        final_result = ''
        # Get the image binary from local URL
        pdf_response = get(input_url)
        pdf_response.raise_for_status()
        content_type = pdf_response.headers['Content-Type']
        if content_type.startswith('application/pdf'):
            pdf_binary = io.BytesIO(pdf_response.content)
        else:
            raise Exception('The URL is not an image')
        files = {'file': ('image', pdf_binary, content_type)}
        # As the RPM of Doc2X is low, we need to try more times when reaching the limit
        uuid = None
        for _ in range(3):
            response = post(
                url,
                headers={'Authorization': f'Bearer {real_api_key}'},
                files=files,
                data={
                    'ocr': ocr,
                },
                timeout=30,
            )
            # The rate limit status code is 429
            if response.status_code == 429:
                time.sleep(10)
                continue
            elif response.status_code != 200:
                raise Exception(response.text)
            else:
                uuid = json.loads(response.content.decode('utf-8'))['data']['uuid']
                break

        if not uuid:
            raise Exception('Failed to upload the image to Doc2X')

        # Try to get the result
        result = None
        if real_api_key.startswith('sk-'):
            url = f'https://api.doc2x.noedgeai.com/api/v1/async/status?uuid={uuid}'
        else:
            url = f'https://api.doc2x.noedgeai.com/api/platform/async/status?uuid={uuid}'
        while True:
            response = get(url, headers={'Authorization': f'Bearer {real_api_key}'}, timeout=30)
            # satus code is 200 means the request is successful
            if response.status_code != 200:
                raise Exception(response.text)
            status = json.loads(response.content.decode('utf-8'))['data']['status']
            if status == 'ready' or status == 'processing':
                time.sleep(1)
                continue
            if status == 'pages limit exceeded':
                raise Exception('Doc2X Pages limit exceeded')
            if status == 'success':
                data = json.loads(response.content.decode('utf-8'))['data']['result']['pages']
                result = ''
                for page in data:
                    result += page['md']
                    result += '\n'
                break
        if not result:
            raise Exception('Failed to get the result from Doc2X')
        final_result += result
        final_result += '\n'
        return self.create_text_message(final_result)
