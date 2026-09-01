"""`python -m md4paper` 진입점 — 데스크톱 런처(.app / .lnk / .desktop)가 이 경로로 실행한다.

가드가 꼭 필요하다. 네이티브 창 모드는 웹뷰를 multiprocessing spawn으로 띄우는데, spawn은 자식
프로세스에서 `__main__` 모듈을 다시 실행한다(`__mp_main__`이라는 이름으로). 가드가 없으면 자식이
CLI를 처음부터 다시 돌려 앱이 무한히 겹쳐 뜬다.
"""

from md4paper.cli import main

if __name__ == "__main__":
    main()
