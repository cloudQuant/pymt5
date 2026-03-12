"""Deep analysis of the binary protocol in D8ROuLSQ.js and BBEi3ZNU.js"""
import re
import os

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))

def load_js(filename):
    with open(os.path.join(ANALYSIS_DIR, filename), 'r', encoding='utf-8') as f:
        return f.read()

def semi_demin(code):
    """Very basic de-minification for readability."""
    code = code.replace(';', ';\n')
    code = code.replace('{', '{\n')
    code = code.replace('}', '}\n')
    return code

def extract_context(text, pattern, context=600):
    results = []
    for m in re.finditer(pattern, text):
        start = max(0, m.start() - context)
        end = min(len(text), m.end() + context)
        results.append({
            'match': m.group(),
            'context': text[start:end],
            'pos': m.start()
        })
    return results

def analyze_websocket_transport():
    """Analyze BBEi3ZNU.js for WebSocket transport layer."""
    print("=" * 70)
    print("WEBSOCKET TRANSPORT LAYER (BBEi3ZNU.js)")
    print("=" * 70)
    
    content = load_js('BBEi3ZNU.js')
    
    # Find WebSocket creation
    for pat in [r'new\s+WebSocket', r'\.binaryType', r'\.onopen', r'\.onclose', r'\.onerror', r'\.onmessage']:
        matches = extract_context(content, pat, 400)
        for m in matches[:2]:
            ctx = semi_demin(m['context'])
            lines = [l.strip() for l in ctx.split('\n') if l.strip()][:15]
            print(f"\n  [{pat}] at pos {m['pos']}:")
            for l in lines:
                print(f"    {l[:150]}")
    
    # Find send function
    matches = extract_context(content, r'\.send\s*\(', 500)
    for m in matches[:3]:
        ctx = semi_demin(m['context'])
        lines = [l.strip() for l in ctx.split('\n') if l.strip()][:15]
        print(f"\n  [.send()] at pos {m['pos']}:")
        for l in lines:
            print(f"    {l[:150]}")

def analyze_binary_serialization():
    """Analyze D8ROuLSQ.js for the binary serialization format."""
    print("\n\n" + "=" * 70)
    print("BINARY SERIALIZATION (D8ROuLSQ.js)")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find the main deserialization function (getInt8, getInt16, etc.)
    matches = extract_context(content, r'getInt8\(', 800)
    for m in matches[:2]:
        ctx = semi_demin(m['context'])
        lines = [l.strip() for l in ctx.split('\n') if l.strip()][:30]
        print(f"\n  [Deserialization] at pos {m['pos']}:")
        for l in lines:
            print(f"    {l[:150]}")
    
    # Find setInt8 (serialization)
    matches = extract_context(content, r'setInt8\(', 800)
    for m in matches[:2]:
        ctx = semi_demin(m['context'])
        lines = [l.strip() for l in ctx.split('\n') if l.strip()][:30]
        print(f"\n  [Serialization] at pos {m['pos']}:")
        for l in lines:
            print(f"    {l[:150]}")

def analyze_message_types():
    """Look for message type definitions and command IDs."""
    print("\n\n" + "=" * 70)
    print("MESSAGE TYPES / COMMAND IDS")
    print("=" * 70)
    
    # Search across key files for command/message type patterns
    for filename in ['D8ROuLSQ.js', 'BBEi3ZNU.js', 'r6gFvn7P.js']:
        content = load_js(filename)
        
        # Look for object literals with numeric values that could be message types
        # Pattern: {something:1,something:2,...}
        enum_matches = re.findall(r'\{([A-Za-z_]\w*:\d+(?:,[A-Za-z_]\w*:\d+){3,})\}', content)
        for em in enum_matches[:5]:
            pairs = em.split(',')
            if len(pairs) > 3:
                print(f"\n  [{filename}] Enum-like object ({len(pairs)} entries):")
                for p in pairs[:20]:
                    print(f"    {p}")
                if len(pairs) > 20:
                    print(f"    ... ({len(pairs) - 20} more)")
        
        # Look for arrays of property definitions
        prop_matches = re.findall(r'propId:(\d+),propType:(\d+)(?:,propName:"([^"]*)")?', content)
        if prop_matches:
            print(f"\n  [{filename}] Property definitions ({len(prop_matches)} found):")
            for pid, ptype, pname in prop_matches[:30]:
                print(f"    propId={pid}, propType={ptype}, propName='{pname}'")
            if len(prop_matches) > 30:
                print(f"    ... ({len(prop_matches) - 30} more)")

def analyze_message_framing():
    """Look for message header/framing structure."""
    print("\n\n" + "=" * 70)
    print("MESSAGE FRAMING / HEADER STRUCTURE")
    print("=" * 70)
    
    for filename in ['BBEi3ZNU.js', 'D8ROuLSQ.js']:
        content = load_js(filename)
        
        # Look for header-related patterns
        for pat in [r'headerSize|header_size|HEADER', r'msgId|msgType|msg_type|commandId|command_id',
                     r'packetSize|packet_size|bodySize|body_size',
                     r'new ArrayBuffer\(\d+\)', r'new DataView\(']:
            matches = extract_context(content, pat, 300)
            for m in matches[:3]:
                ctx = semi_demin(m['context'])
                lines = [l.strip() for l in ctx.split('\n') if l.strip()][:10]
                print(f"\n  [{filename}][{pat}] at pos {m['pos']}:")
                for l in lines:
                    print(f"    {l[:150]}")

def analyze_auth_flow():
    """Look for authentication-related code."""
    print("\n\n" + "=" * 70)
    print("AUTHENTICATION FLOW")
    print("=" * 70)
    
    for filename in ['Dpfv1pCY.js', 'DOwdM2Va.js', 'DMtZQUDi.js']:
        content = load_js(filename)
        
        for pat in [r'password|Password', r'login|Login', r'server|Server',
                     r'auth_start|auth_answer|AUTH', r'hash|Hash|md5|MD5|sha|SHA']:
            matches = extract_context(content, pat, 300)
            if matches:
                print(f"\n  [{filename}][{pat}] {len(matches)} matches, first:")
                ctx = semi_demin(matches[0]['context'])
                lines = [l.strip() for l in ctx.split('\n') if l.strip()][:8]
                for l in lines:
                    print(f"    {l[:150]}")

def analyze_crypto():
    """Analyze CQ67ee1G.js for crypto operations."""
    print("\n\n" + "=" * 70)
    print("CRYPTO / ENCRYPTION (CQ67ee1G.js)")
    print("=" * 70)
    
    content = load_js('CQ67ee1G.js')
    
    for pat in [r'encrypt|Encrypt', r'decrypt|Decrypt', r'AES|aes', r'RSA|rsa',
                r'SubtleCrypto|subtle', r'importKey|deriveKey|deriveBits']:
        matches = extract_context(content, pat, 300)
        if matches:
            print(f"\n  [{pat}] {len(matches)} matches")
            ctx = semi_demin(matches[0]['context'])
            lines = [l.strip() for l in ctx.split('\n') if l.strip()][:8]
            for l in lines:
                print(f"    {l[:150]}")

if __name__ == '__main__':
    analyze_websocket_transport()
    analyze_binary_serialization()
    analyze_message_types()
    analyze_message_framing()
    analyze_auth_flow()
    analyze_crypto()
