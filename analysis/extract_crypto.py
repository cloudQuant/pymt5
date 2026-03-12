"""Extract encryption/decryption details from BBEi3ZNU.js and CQ67ee1G.js"""
import re
import os

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))

def load_js(filename):
    with open(os.path.join(ANALYSIS_DIR, filename), 'r', encoding='utf-8') as f:
        return f.read()

def semi_demin(code):
    code = code.replace(';', ';\n')
    code = code.replace('{', '{\n')
    code = code.replace('}', '}\n')
    return code

def extract_context(text, pos, before=300, after=1500):
    start = max(0, pos - before)
    end = min(len(text), pos + after)
    return text[start:end]

def analyze_enc_dec():
    """Find enc/dec/initKey functions."""
    print("=" * 70)
    print("ENCRYPTION: enc / dec / initKey")
    print("=" * 70)
    
    # The enc/dec functions are imported - find them
    for filename in ['BBEi3ZNU.js', 'CQ67ee1G.js', 'D8ROuLSQ.js', 'DtUlhiPz.js']:
        content = load_js(filename)
        
        for pat in [r'initKey\s*[\(:]', r'function\s+\w*enc\w*\s*\(', r'function\s+\w*dec\w*\s*\(',
                     r'enc\s*:\s*(?:async\s+)?function', r'dec\s*:\s*(?:async\s+)?function',
                     r'enc\s*\(', r'initKey\s*\(']:
            matches = list(re.finditer(pat, content))
            for m in matches[:2]:
                ctx = extract_context(content, m.start(), 200, 1000)
                lines = [l.strip() for l in semi_demin(ctx).split('\n') if l.strip()][:25]
                print(f"\n  [{filename}] '{pat}' at pos {m.start()}:")
                for l in lines:
                    print(f"    {l[:160]}")

def analyze_subtle_crypto():
    """Find Web Crypto API usage."""
    print("\n\n" + "=" * 70)
    print("WEB CRYPTO API")
    print("=" * 70)
    
    for filename in ['BBEi3ZNU.js', 'CQ67ee1G.js']:
        content = load_js(filename)
        
        for pat in [r'crypto\.subtle', r'importKey', r'deriveKey', r'deriveBits',
                     r'encrypt\s*\(', r'decrypt\s*\(', r'AES-', r'PBKDF2', r'HKDF',
                     r'AES-GCM|AES-CBC|AES-CTR', r'SHA-256|SHA-512|SHA-1']:
            matches = list(re.finditer(pat, content))
            if matches:
                ctx = extract_context(content, matches[0].start(), 200, 600)
                lines = [l.strip() for l in semi_demin(ctx).split('\n') if l.strip()][:15]
                print(f"\n  [{filename}] '{pat}' ({len(matches)} matches):")
                for l in lines:
                    print(f"    {l[:160]}")

def analyze_sendcommand():
    """Find the full sendCommand implementation."""
    print("\n\n" + "=" * 70)
    print("sendCommand IMPLEMENTATION")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    idx = content.find('sendCommand')
    while idx >= 0:
        ctx = extract_context(content, idx, 200, 1200)
        lines = [l.strip() for l in semi_demin(ctx).split('\n') if l.strip()][:30]
        print(f"\n  sendCommand at pos {idx}:")
        for l in lines:
            print(f"    {l[:160]}")
        idx = content.find('sendCommand', idx + 100)
        if idx > 0 and idx - content.find('sendCommand') > 5000:
            break

def analyze_all_commands_in_detail():
    """Find all sendCommand calls with their schemas."""
    print("\n\n" + "=" * 70)
    print("ALL sendCommand CALLS WITH CONTEXT")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find all sendCommand(NUMBER, ...) patterns
    for m in re.finditer(r'sendCommand\s*\(\s*(\d+)', content):
        cmd_id = m.group(1)
        ctx = extract_context(content, m.start(), 400, 300)
        # Try to find function name
        func_match = re.search(r'(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{[^}]*$', ctx[:ctx.find('sendCommand')])
        func_name = func_match.group(1) if func_match else "?"
        
        lines = [l.strip() for l in semi_demin(ctx).split('\n') if l.strip()]
        print(f"\n  cmd={cmd_id} (near '{func_name}'):")
        for l in lines[:15]:
            print(f"    {l[:160]}")

def analyze_parse_schemas():
    """Find Ec.parse calls with their schemas."""
    print("\n\n" + "=" * 70)
    print("PARSE SCHEMAS (Ec.parse calls)")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find all Ec.parse(X, SCHEMA) calls
    for m in re.finditer(r'Ec\.parse\(\w+,(\w+)\)', content):
        schema_name = m.group(1)
        # Find schema definition
        schema_idx = content.find(f'{schema_name}=[')
        if schema_idx < 0:
            schema_idx = content.find(f'{schema_name} =[')
        if schema_idx >= 0:
            ctx = extract_context(content, schema_idx, 50, 500)
            lines = [l.strip() for l in semi_demin(ctx).split('\n') if l.strip()][:15]
            print(f"\n  Schema '{schema_name}':")
            for l in lines:
                print(f"    {l[:160]}")
        
        # Also find the Ec.serialize calls near the same function
        ctx = extract_context(content, m.start(), 600, 100)
        serialize_match = re.search(r'Ec\.serialize\((\[.{0,500}?\])\)', ctx)
        if serialize_match:
            print(f"  Serialize near parse: {serialize_match.group(1)[:200]}")

if __name__ == '__main__':
    analyze_enc_dec()
    analyze_subtle_crypto()
    analyze_sendcommand()
    analyze_all_commands_in_detail()
    analyze_parse_schemas()
