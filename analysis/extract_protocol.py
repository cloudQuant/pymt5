"""Extract WebSocket and binary protocol patterns from MT5 Web Terminal JS files."""
import re
import os
import json

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))

def load_js(filename):
    with open(os.path.join(ANALYSIS_DIR, filename), 'r', encoding='utf-8') as f:
        return f.read()

def extract_around(text, pattern, context=300):
    """Extract code snippets around pattern matches."""
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

def analyze_file(filename):
    content = load_js(filename)
    size = len(content)
    
    patterns = {
        'websocket': r'WebSocket|\.send\(|onmessage|\.close\(',
        'binary': r'ArrayBuffer|DataView|Uint8Array|Int32Array|Float64Array|getUint8|getInt32|getFloat64|setUint8|setInt32|setFloat64',
        'crypto': r'crypto|encrypt|decrypt|hash|md5|sha|hmac|digest',
        'auth': r'auth|login|password|credential|challenge|handshake',
        'protocol_header': r'header|packet|frame|payload|opcode|msgtype|msg_type|command',
        'endian': r'littleEndian|bigEndian|LE|BE',
        'trade': r'order|position|deal|trade|buy|sell|volume|lot',
        'tick': r'bid|ask|spread|tick|quote|price',
        'account': r'balance|equity|margin|leverage|account',
    }
    
    report = {'file': filename, 'size': size, 'findings': {}}
    
    for name, pat in patterns.items():
        matches = extract_around(content, pat, context=200)
        if matches:
            report['findings'][name] = {
                'count': len(matches),
                'samples': [m['context'][:300] for m in matches[:5]]
            }
    
    return report

def main():
    js_files = sorted([f for f in os.listdir(ANALYSIS_DIR) if f.endswith('.js')])
    
    print(f"Analyzing {len(js_files)} JS files...\n")
    
    all_reports = []
    for f in js_files:
        report = analyze_file(f)
        all_reports.append(report)
        
        findings = report['findings']
        if findings:
            print(f"\n{'='*60}")
            print(f"FILE: {f} ({report['size']:,} bytes)")
            print(f"{'='*60}")
            for category, data in sorted(findings.items()):
                print(f"  [{category}] {data['count']} matches")
    
    # Find the main WebSocket/protocol file
    print(f"\n\n{'='*60}")
    print("KEY FILES FOR PROTOCOL REVERSE ENGINEERING:")
    print(f"{'='*60}")
    
    for r in all_reports:
        f = r['findings']
        ws_count = f.get('websocket', {}).get('count', 0)
        bin_count = f.get('binary', {}).get('count', 0)
        crypto_count = f.get('crypto', {}).get('count', 0)
        
        if ws_count + bin_count > 5:
            print(f"\n>>> {r['file']} ({r['size']:,} bytes)")
            print(f"    WebSocket: {ws_count}, Binary: {bin_count}, Crypto: {crypto_count}")
            
            # Print binary operation samples
            if 'binary' in f:
                print(f"    Binary samples:")
                for i, s in enumerate(f['binary']['samples'][:3]):
                    # Clean up the sample
                    clean = s.replace('\n', ' ')[:200]
                    print(f"      [{i}] ...{clean}...")
    
    # Deep dive into the main protocol file
    print(f"\n\n{'='*60}")
    print("DEEP DIVE: WebSocket message handling")
    print(f"{'='*60}")
    
    for r in all_reports:
        if r['findings'].get('websocket', {}).get('count', 0) > 0:
            content = load_js(r['file'])
            # Find WebSocket creation and message handler
            ws_patterns = [
                r'new\s+WebSocket\s*\([^)]+\)',
                r'\.onmessage\s*=',
                r'\.binaryType\s*=',
                r'\.send\s*\(',
            ]
            for pat in ws_patterns:
                matches = extract_around(content, pat, context=500)
                for m in matches[:2]:
                    print(f"\n  Pattern: {pat}")
                    print(f"  File: {r['file']}")
                    print(f"  Context ({len(m['context'])} chars):")
                    # Try to make it readable
                    ctx = m['context']
                    # Add newlines at semicolons for readability
                    ctx_fmt = ctx.replace(';', ';\n    ')[:1000]
                    print(f"    {ctx_fmt}")

    # Search for message type constants / opcodes
    print(f"\n\n{'='*60}")
    print("MESSAGE TYPE CONSTANTS / OPCODES")
    print(f"{'='*60}")
    
    for r in all_reports:
        content = load_js(r['file'])
        # Look for enum-like patterns with sequential numbers
        enum_patterns = [
            r'(\w+)\s*=\s*(\d+)\s*[,;]',  # var = number
            r'(\d+)\s*:\s*["\'](\w+)["\']',  # number: "name"
            r'["\'](\w+)["\']\s*:\s*(\d+)',  # "name": number
        ]
        
        # Look for switch statements on message types
        switch_matches = extract_around(content, r'switch\s*\([^)]*\)\s*\{', context=1000)
        for m in switch_matches[:3]:
            cases = re.findall(r'case\s+(\d+)\s*:', m['context'])
            if len(cases) > 5:  # Likely a message type switch
                print(f"\n  File: {r['file']}, pos: {m['pos']}")
                print(f"  Switch cases: {cases[:30]}")
                # Show some of the case bodies
                case_snippets = re.findall(r'case\s+(\d+)\s*:([^}]{0,200})', m['context'])
                for case_num, body in case_snippets[:10]:
                    body_clean = body.replace('\n', ' ').strip()[:100]
                    print(f"    case {case_num}: {body_clean}")

if __name__ == '__main__':
    main()
