"""Extract exact command IDs, property definitions, and auth packet structure."""
import re
import os

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))

def load_js(filename):
    with open(os.path.join(ANALYSIS_DIR, filename), 'r', encoding='utf-8') as f:
        return f.read()

def extract_context(text, pos, before=500, after=1500):
    start = max(0, pos - before)
    end = min(len(text), pos + after)
    return text[start:end]

def semi_demin(code):
    code = code.replace(';', ';\n')
    code = code.replace('{', '{\n')
    code = code.replace('}', '}\n')
    return code

def find_command_header():
    """Extract the command() function and HEADER_BYTE_LENGTH from D8ROuLSQ.js"""
    print("=" * 70)
    print("1. MESSAGE HEADER STRUCTURE")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find the command function
    idx = content.find('command:function(t,e)')
    if idx >= 0:
        ctx = extract_context(content, idx, 100, 800)
        print("\ncommand() function:")
        for line in semi_demin(ctx).split('\n')[:20]:
            line = line.strip()
            if line:
                print(f"  {line[:150]}")
    
    # Find HEADER_BYTE_LENGTH 
    idx = content.find('HEADER_BYTE_LENGTH')
    if idx >= 0:
        ctx = extract_context(content, idx, 200, 200)
        print(f"\nHEADER_BYTE_LENGTH context:")
        for line in semi_demin(ctx).split('\n')[:15]:
            line = line.strip()
            if line:
                print(f"  {line[:150]}")

    # Find the 8-byte outer framing
    # Look for first 8 bytes pattern - likely includes length
    for pat in [r'setUint32\(0,', r'setUint32\(4,']:
        matches = list(re.finditer(pat, content))
        for m in matches[:3]:
            ctx = extract_context(content, m.start(), 100, 300)
            print(f"\n{pat} at pos {m.start()}:")
            for line in semi_demin(ctx).split('\n')[:10]:
                line = line.strip()
                if line:
                    print(f"  {line[:150]}")

def find_login_packet():
    """Extract the login/auth packet construction."""
    print("\n\n" + "=" * 70)
    print("2. LOGIN PACKET STRUCTURE")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find where the login packet is built
    idx = content.find('t.version||0')
    if idx >= 0:
        ctx = extract_context(content, idx, 500, 2000)
        print("\nLogin packet construction:")
        for line in semi_demin(ctx).split('\n')[:50]:
            line = line.strip()
            if line:
                print(f"  {line[:150]}")

def find_all_api_calls():
    """Find all API method calls with their command IDs."""
    print("\n\n" + "=" * 70)
    print("3. API COMMAND IDS")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Pattern: command(NUMBER, ...) or .command(NUMBER
    cmd_calls = re.findall(r'\.command\((\d+)', content)
    if cmd_calls:
        unique_cmds = sorted(set(int(c) for c in cmd_calls))
        print(f"\nAll command IDs found ({len(unique_cmds)} unique):")
        for cmd in unique_cmds:
            count = cmd_calls.count(str(cmd))
            # Find context around first occurrence
            idx = content.find(f'.command({cmd}')
            if idx >= 0:
                ctx = content[max(0,idx-200):idx+300]
                # Try to find the function name
                func_match = re.search(r'(\w+)\s*(?:=\s*(?:async\s+)?function|\()', ctx[:200])
                func_name = func_match.group(1) if func_match else "?"
                print(f"  cmd={cmd:4d} (x{count}) near: ...{ctx[max(0,len(ctx)//2-80):len(ctx)//2+80].strip()[:160]}...")

    # Also look for this.request or api.request patterns
    for pat in [r'request\s*\(\s*(\d+)', r'\.send\s*\(\s*(\d+)']:
        matches = re.findall(pat, content)
        if matches:
            unique = sorted(set(int(m) for m in matches))
            print(f"\n  Pattern '{pat}': {unique}")

def find_response_handlers():
    """Find how responses are dispatched based on command IDs."""
    print("\n\n" + "=" * 70)
    print("4. RESPONSE HANDLERS / EVENT TYPES")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find listener registration patterns
    # Pattern: .listen(NUMBER, ...) or .on(NUMBER, ...)
    for pat in [r'\.listen\s*\(\s*(\d+)', r'\.on\s*\(\s*(\d+)', r'\.subscribe\s*\(\s*(\d+)']:
        matches = re.findall(pat, content)
        if matches:
            unique = sorted(set(int(m) for m in matches))
            print(f"\n  Pattern '{pat}': {unique}")
    
    # Find the message dispatch function
    idx = content.find('resCode')
    if idx >= 0:
        ctx = extract_context(content, idx, 500, 500)
        print(f"\nResponse dispatch (near resCode):")
        for line in semi_demin(ctx).split('\n')[:20]:
            line = line.strip()
            if line:
                print(f"  {line[:150]}")

def find_property_schemas():
    """Find property schemas that define message structures."""
    print("\n\n" + "=" * 70)
    print("5. PROPERTY SCHEMAS (Message Definitions)")
    print("=" * 70)
    
    content = load_js('D8ROuLSQ.js')
    
    # Find propId/propType definitions
    # These define the structure of each message type
    prop_defs = re.findall(
        r'\{propId:(\d+),propType:(\d+)(?:,propName:"([^"]*)")?(?:,propLength:(\d+))?(?:,propLengthType:(\d+))?[^}]*\}',
        content
    )
    
    if prop_defs:
        print(f"\nProperty definitions ({len(prop_defs)} total):")
        for pid, ptype, pname, plen, pltype in prop_defs[:60]:
            info = f"  propId={pid:4s}, propType={ptype:2s}"
            if pname:
                info += f', propName="{pname}"'
            if plen:
                info += f', propLength={plen}'
            if pltype:
                info += f', propLengthType={pltype}'
            print(info)
        if len(prop_defs) > 60:
            print(f"  ... ({len(prop_defs) - 60} more)")
    
    # Find schema arrays (groups of properties together)
    # Pattern: [{propId:...,propType:...},{propId:...,propType:...},...]
    schema_starts = list(re.finditer(r'\[\{propId:', content))
    print(f"\n\nSchema definitions ({len(schema_starts)} found):")
    for m in schema_starts[:20]:
        # Extract the full array
        bracket_count = 0
        end_pos = m.start()
        for i in range(m.start(), min(m.start() + 3000, len(content))):
            if content[i] == '[':
                bracket_count += 1
            elif content[i] == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    end_pos = i + 1
                    break
        
        schema_text = content[m.start():end_pos]
        props = re.findall(r'propId:(\d+),propType:(\d+)(?:,propName:"([^"]*)")?', schema_text)
        
        # Try to find what this schema is for
        prefix = content[max(0, m.start()-200):m.start()]
        # Look for variable assignment
        var_match = re.search(r'(\w+)\s*=\s*$', prefix)
        schema_name = var_match.group(1) if var_match else "?"
        
        if props:
            print(f"\n  Schema '{schema_name}' ({len(props)} props) at pos {m.start()}:")
            for pid, ptype, pname in props[:15]:
                type_names = {
                    '1':'int8','2':'int16','3':'int32',
                    '4':'uint8','5':'uint16','6':'uint32',
                    '7':'float32','8':'float64','9':'int64',
                    '10':'string','11':'fixedString','12':'bytes',
                    '17':'BigInt64','18':'BigUint64'
                }
                tname = type_names.get(ptype, f'type{ptype}')
                info = f"    [{pid:3s}] {tname:12s}"
                if pname:
                    info += f' "{pname}"'
                print(info)
            if len(props) > 15:
                print(f"    ... ({len(props) - 15} more)")

def find_compression():
    """Find compression/decompression logic."""
    print("\n\n" + "=" * 70)
    print("6. COMPRESSION / DECOMPRESSION")
    print("=" * 70)
    
    for filename in ['D8ROuLSQ.js', 'BBEi3ZNU.js', 'DtUlhiPz.js']:
        content = load_js(filename)
        for pat in [r'inflate|deflate|pako|zlib', r'\.dec\s*\(', r'\.enc\s*\(', 
                     r'decompress|compress']:
            matches = list(re.finditer(pat, content, re.IGNORECASE))
            if matches:
                print(f"\n  [{filename}] Pattern '{pat}': {len(matches)} matches")
                ctx = extract_context(content, matches[0].start(), 200, 400)
                for line in semi_demin(ctx).split('\n')[:10]:
                    line = line.strip()
                    if line:
                        print(f"    {line[:150]}")

if __name__ == '__main__':
    find_command_header()
    find_login_packet()
    find_all_api_calls()
    find_response_handlers()
    find_property_schemas()
    find_compression()
