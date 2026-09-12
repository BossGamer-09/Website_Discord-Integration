import aiohttp
import io


async def get_file_from_url(url):
    # Add headers to accept Brotli encoding
    headers = {
        'Accept-Encoding': 'gzip, deflate, br'
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            # Check if response is Brotli encoded
            if resp.headers.get('Content-Encoding') == 'br':
                # We need to handle Brotli decompression
                # Note: aiohttp handles gzip/deflate automatically, but not Brotli
                # We'll need to decompress manually
                import brotli
                compressed_data = await resp.read()
                img = brotli.decompress(compressed_data)
            else:
                img = await resp.read()

            f = io.BytesIO(img)
            return f
