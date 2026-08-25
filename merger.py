import os
import re
import shutil
from collections import defaultdict


class FileMerger:

    @staticmethod
    def _find_missing_indices(sorted_indices):

        if not sorted_indices:
            return []

        missing = []

        for i in range(len(sorted_indices) - 1):

            current_index = sorted_indices[i]
            next_index = sorted_indices[i + 1]

            if next_index - current_index > 1:

                missing.extend(
                    range(current_index + 1, next_index)
                )

        return missing

    @staticmethod
    def parse_filename(filename):
        """
        文件名格式：
        xxx_0.txt
        返回：
        prefix, index, ext
        """

        name, ext = os.path.splitext(filename)

        m = re.match(r"(.+?)_(\d+)$", name)

        if not m:
            return None

        prefix = m.group(1)
        index = int(m.group(2))

        return prefix, index, ext

    @staticmethod
    def group_files(file_list):

        groups = defaultdict(list)

        for file in file_list:

            result = FileMerger.parse_filename(os.path.basename(file))

            if result is None:
                continue

            prefix, index, ext = result

            groups[(prefix, ext)].append(
                (
                    index,
                    file
                )
            )

        return groups

    @staticmethod
    def merge(file_list, output_dir):

        groups = FileMerger.group_files(file_list)

        logs = []

        os.makedirs(output_dir, exist_ok=True)

        for (prefix, ext), items in groups.items():

            items.sort(key=lambda x: x[0])

            sorted_indices = [index for index, _ in items]
            missing_indices = FileMerger._find_missing_indices(
                sorted_indices
            )

            if missing_indices:

                logs.append(
                    f"{prefix} -> Skipped: missing chunk(s) {', '.join(map(str, missing_indices))}"
                )

                continue

            output_file = os.path.join(
                output_dir,
                f"{prefix}_merge{ext}"
            )

            with open(output_file, "wb") as outfile:

                for _, file in items:

                    with open(file, "rb") as infile:

                        shutil.copyfileobj(infile, outfile)

            logs.append(
                f"{prefix} -> Merge complete, {len(items)} chunk(s)"
            )

        return logs