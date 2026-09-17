# -*- coding: utf-8 -*-
"""
claude_env_check.py — Claude Code 작업 환경 드리프트 검사 (이식용 참조 구현)

사양의 기준은 같은 저장소의 AI-GUIDE.md §5 다. 이 파일은 그 사양을 구현한 예시이며,
환경에 없는 층(메모리·스킬·settings 미러·자격증명 파일)은 자동으로 SKIP 처리한다.

사용법 (작업 폴더 루트에서 실행):
    python claude_env_check.py                       # 검사만
    python claude_env_check.py --mirror              # 검사 + 메모리를 작업 폴더 안으로 복사
    python claude_env_check.py --root "D:\\work"      # 작업 폴더를 직접 지정
    python claude_env_check.py --memory "<경로>"      # 메모리 폴더를 직접 지정
    python claude_env_check.py --mirror-dir "docs/memory-mirror"  # 복사 위치 변경 (기본 memory-mirror)

종료코드: 실패 항목이 있으면 1, 없으면 0.
"""
import sys, io, os, re, json, glob, shutil, filecmp, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 한도 값 (AI-GUIDE.md §1 — Claude Code 버전에 따라 바뀔 수 있으니 필요하면 여기서 조정)
CLAUDE_MD_MAX_LINES = 200
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024


def parse_args():
    p = argparse.ArgumentParser(description='Claude Code 작업 환경 드리프트 검사')
    p.add_argument('--root', default=os.getcwd(), help='작업 폴더 루트 (기본: 현재 폴더)')
    p.add_argument('--memory', default=None, help='메모리 폴더 (기본: 작업 폴더 경로에서 자동 추정)')
    p.add_argument('--mirror', action='store_true', help='메모리를 작업 폴더 안으로 복사')
    p.add_argument('--mirror-dir', default='memory-mirror', help='복사 위치 (작업 폴더 기준 상대경로)')
    return p.parse_args()


def guess_memory_dir(root):
    """Claude Code 는 작업 폴더 절대경로의 영숫자 아닌 글자를 '-' 로 바꾼 이름으로 프로젝트 폴더를 만든다."""
    projects = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
    encoded = re.sub(r'[^A-Za-z0-9]', '-', os.path.abspath(root))
    cand = os.path.join(projects, encoded, 'memory')
    if os.path.isdir(cand):
        return cand
    # 드라이브 문자 대소문자 차이 등으로 못 찾으면 대소문자 무시하고 다시 찾는다
    if os.path.isdir(projects):
        for d in os.listdir(projects):
            if d.lower() == encoded.lower() and os.path.isdir(os.path.join(projects, d, 'memory')):
                return os.path.join(projects, d, 'memory')
    return None


def read(p):
    return open(p, encoding='utf-8').read()


def main():
    a = parse_args()
    root = os.path.abspath(a.root)
    mem = a.memory or guess_memory_dir(root)
    results = []  # (항목, 상태 'OK'|'FAIL'|'SKIP', 상세)

    def check(name, ok, detail=''):
        results.append((name, 'OK' if ok else 'FAIL', detail))

    def skip(name, why):
        results.append((name, 'SKIP', why))

    # 1. CLAUDE.md 행수
    cm = os.path.join(root, 'CLAUDE.md')
    if os.path.isfile(cm):
        n = len(read(cm).splitlines())
        check(f'CLAUDE.md < {CLAUDE_MD_MAX_LINES}행', n < CLAUDE_MD_MAX_LINES, f'{n}행')
    else:
        check('CLAUDE.md 존재', False, f'{cm} 없음 — --root 로 작업 폴더를 지정하세요')

    # 2~8. 메모리
    files = set()
    idx_path = os.path.join(mem, 'MEMORY.md') if mem else None
    if not mem or not os.path.isfile(idx_path):
        for nm in ('인덱스 행수', '인덱스 크기', '인덱스↔파일 정합', 'name == 파일명', '[[링크]] 단절', 'type 누락'):
            skip(nm, '메모리 폴더를 찾지 못함 (쓰지 않는 환경이면 정상, 쓰는 환경이면 --memory 로 지정)')
    else:
        idx = read(idx_path)
        n_lines, n_bytes = len(idx.splitlines()), os.path.getsize(idx_path)
        check(f'MEMORY.md < {INDEX_MAX_LINES}행', n_lines < INDEX_MAX_LINES, f'{n_lines}행')
        check(f'MEMORY.md < {INDEX_MAX_BYTES // 1024}KB', n_bytes < INDEX_MAX_BYTES,
              f'{n_bytes}B ({n_bytes / INDEX_MAX_BYTES * 100:.0f}%)')

        indexed = set(re.findall(r'\]\(([^)]+\.md)\)', idx))
        files = {os.path.basename(f) for f in glob.glob(os.path.join(mem, '*.md'))} - {'MEMORY.md'}
        missing, orphan = sorted(indexed - files), sorted(files - indexed)
        check('인덱스에 있으나 파일 없음 0', not missing, ', '.join(missing[:5]))
        check('파일 있으나 인덱스 없음 0', not orphan, ', '.join(orphan[:5]))

        names, mismatch, notype, links = set(), [], [], set()
        for f in sorted(files):
            body = read(os.path.join(mem, f))
            fm = body.split('---')[1] if body.startswith('---') and body.count('---') >= 2 else ''
            m = re.search(r'^name:\s*(.+?)\s*$', fm, re.M)
            name = m.group(1) if m else ''
            names.add(name)
            if name != f[:-3]:
                mismatch.append(f'{f} (name: {name or "없음"})')
            # 규격: metadata 아래 들여쓴 type. 최상위 type 은 오래된 형식이라 누락으로 본다.
            if not re.search(r'^\s+type:\s*\S', fm, re.M):
                notype.append(f)
            links.update(re.findall(r'\[\[([^\]|#]+)', body))
        dangling = sorted(l for l in links if l not in names)

        def brief(items):
            return f'{len(items)}건: ' + ', '.join(items[:5]) + (' …' if len(items) > 5 else '')
        check('name == 파일명', not mismatch, brief(mismatch) if mismatch else '')
        check('[[링크]] 단절 0', not dangling, brief(dangling) if dangling else '')
        check('type 누락 0', not notype, brief(notype) if notype else '')

    # 9. settings 미러
    sj = os.path.join(root, '.claude', 'settings.json')
    sl = os.path.join(root, '.claude', 'settings.local.json')
    if os.path.isfile(sj) and os.path.isfile(sl):
        try:
            pa = json.load(open(sj, encoding='utf-8')).get('permissions', {})
            pb = json.load(open(sl, encoding='utf-8')).get('permissions', {})
            diffs = []
            for k in sorted(set(pa) | set(pb)):
                va, vb = pa.get(k) or [], pb.get(k) or []
                if isinstance(va, list) and isinstance(vb, list):
                    sa, sb = set(map(str, va)), set(map(str, vb))
                    if sa != sb:
                        diffs.append(f'{k}: json만 {len(sa - sb)} / local만 {len(sb - sa)}')
                elif va != vb:
                    diffs.append(f'{k}: 값 다름')
            check('settings.json == settings.local.json (permissions)', not diffs, '; '.join(diffs))
        except json.JSONDecodeError as e:
            check('settings 파일 JSON 형식', False, f'{e}. 파일을 열어 쉼표·따옴표를 확인하세요')
    else:
        skip('settings 미러', 'settings 파일이 하나뿐이거나 없음 (두 파일을 같이 운용하지 않으면 정상)')

    # 10. 자격증명 백업
    cdir = os.path.join(root, '.claude')
    if os.path.isdir(cdir):
        baks = [os.path.basename(p) for p in glob.glob(os.path.join(cdir, 'env*'))
                if re.search(r'\.bak', os.path.basename(p))]
        check('.claude/ 자격증명 백업 파일 없음', not baks, ', '.join(baks[:5]))
    else:
        skip('자격증명 백업', '.claude 폴더 없음')

    # 11. skills
    sdir = os.path.join(root, '.claude', 'skills')
    if os.path.isdir(sdir):
        skills = glob.glob(os.path.join(sdir, '*', 'SKILL.md'))
        check('.claude/skills/ 에 SKILL.md 있음', bool(skills), f'{len(skills)}개')
    else:
        skip('skills', '.claude/skills 폴더 없음 (스킬을 쓰지 않으면 정상)')

    # 출력
    n_fail = sum(1 for _, s, _ in results if s == 'FAIL')
    n_ok = sum(1 for _, s, _ in results if s == 'OK')
    n_skip = sum(1 for _, s, _ in results if s == 'SKIP')
    for name, state, detail in results:
        print(f'{state:4} {name}' + (f' — {detail}' if detail else ''))
    print(f'\n결과: 통과 {n_ok} / 실패 {n_fail} / 해당 없음 {n_skip}')
    print(f'작업 폴더: {root}')
    print(f'메모리: {mem or "(찾지 못함)"}')

    # 미러: 작업 폴더 경로에 묶인 메모리를 작업 폴더 안에 복사해 백업·버전 관리 대상에 넣는다
    if a.mirror:
        if not mem:
            print('미러 건너뜀: 메모리 폴더를 찾지 못했습니다. --memory 로 지정하세요.')
        else:
            dst_dir = os.path.join(root, a.mirror_dir)
            os.makedirs(dst_dir, exist_ok=True)
            changed = 0
            all_files = files | {'MEMORY.md'}
            for f in sorted(all_files):
                src, dst = os.path.join(mem, f), os.path.join(dst_dir, f)
                if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
                    shutil.copy2(src, dst)
                    changed += 1
            stale = sorted({os.path.basename(p) for p in glob.glob(os.path.join(dst_dir, '*.md'))} - all_files)
            for f in stale:  # 원본에서 지워진 메모리는 복사본에서도 지운다
                os.remove(os.path.join(dst_dir, f))
            print(f'미러: {len(all_files)}개 동기화 (변경 {changed}, 제거 {len(stale)}) → {os.path.relpath(dst_dir, root)}')

    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
