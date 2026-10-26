import asyncio
import json
import sys
from .agent import CodeAgent

async def main():
    question = ' '.join(sys.argv[1:]) or input('Debugging question: ')
    finding = await CodeAgent().run(question, {})
    print(finding.model_dump_json(indent=2))

if __name__ == '__main__':
    asyncio.run(main())
