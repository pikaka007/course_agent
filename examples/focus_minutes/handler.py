import json,sys
a=json.load(sys.stdin)
print(json.dumps({'total_minutes':a['rounds']*a['minutes']}))
