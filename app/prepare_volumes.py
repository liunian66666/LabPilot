"""一次性修正专用数据卷属主；主应用始终以非 root 用户运行。"""

import os
import stat
from pathlib import Path


def prepare_paths(roots: list[Path], uid: int, gid: int) -> None:
    paths = []
    for root in roots:
        if not root.is_absolute() or root.resolve() != root or not root.is_dir():
            raise RuntimeError(
                "Expected an existing absolute volume directory without symlinks"
            )
        paths.append(root)
        for directory, names, files in os.walk(root, followlinks=False):
            for name in [*names, *files]:
                path = Path(directory) / name
                if path.is_symlink():
                    raise RuntimeError(
                        "Refusing to change a volume containing symlinks"
                    )
                if not (path.is_dir() or path.is_file()):
                    raise RuntimeError("Unexpected special file in application volume")
                paths.append(path)
    # 全部检查完才修改，只涉及这两个专用卷，不跟随链接。
    for path in paths:
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, stat.S_IRWXU if path.is_dir() else stat.S_IRUSR | stat.S_IWUSR)


def main():
    import pwd

    account = pwd.getpwnam("labpilot")
    prepare_paths(
        [Path("/app/data"), Path("/app/logs")], account.pw_uid, account.pw_gid
    )
    print("Application volume ownership prepared; database contents unchanged.")


if __name__ == "__main__":
    main()
