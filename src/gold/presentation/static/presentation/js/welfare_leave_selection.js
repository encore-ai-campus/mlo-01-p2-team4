(function () {
    "use strict";

    const grantForm = document.getElementById("grant-form");
    if (!grantForm) {
        return;
    }

    const targetSelectors = Array.from(
        document.querySelectorAll(".target-selector")
    );
    const selectionSummary = document.querySelector(".select-all-summary");
    const selectAllTargets = document.getElementById("select-all-targets");
    const selectionMode = grantForm.querySelector("#id_selection_mode");
    const selectionPayload = document.getElementById("selection-payload");
    const confirmAll = grantForm.querySelector("#id_confirm_all");
    const grantButton = grantForm.querySelector(".grant-button");
    const countTargets = [
        document.getElementById("selected-target-count"),
        document.getElementById("grant-target-count"),
        document.getElementById("confirmation-target-count"),
    ].filter(Boolean);

    if (
        !selectionSummary
        || !selectionMode
        || !selectionPayload
        || !confirmAll
        || !grantButton
    ) {
        return;
    }

    const selectionScopeCount = Number(
        selectionSummary.dataset.selectionScopeCount || 0
    );
    const serverReady = grantButton.dataset.serverReady === "true";
    const manuallySelected = new Set();
    const excludedFromAll = new Set();
    let allSelected = false;

    /** 현재 선택 상태를 서버 Form에서 읽을 수 있는 숨김 입력으로 변환합니다. */
    function synchronizePayload() {
        selectionMode.value = allSelected ? "all" : "manual";
        selectionPayload.replaceChildren();
        const inputName = allSelected
            ? "excluded_employee_numbers"
            : "selected_employee_numbers";
        const employeeNumbers = allSelected
            ? excludedFromAll
            : manuallySelected;

        employeeNumbers.forEach((employeeNo) => {
            const hiddenInput = document.createElement("input");
            hiddenInput.type = "hidden";
            hiddenInput.name = inputName;
            hiddenInput.value = employeeNo;
            selectionPayload.appendChild(hiddenInput);
        });
    }

    /** 전체 선택 여부와 예외 목록을 반영해 현재 선택된 전체 인원 수를 반환합니다. */
    function selectedCount() {
        return allSelected
            ? Math.max(selectionScopeCount - excludedFromAll.size, 0)
            : manuallySelected.size;
    }

    /** 선택 인원, 행 강조, 전체 선택 상태와 저장 버튼 활성 조건을 갱신합니다. */
    function updateSelectionState() {
        const currentSelectedCount = selectedCount();

        countTargets.forEach((element) => {
            element.textContent = `${currentSelectedCount}명`;
        });

        targetSelectors.forEach((checkbox) => {
            const employeeNo = checkbox.value;
            checkbox.checked = allSelected
                ? !excludedFromAll.has(employeeNo)
                : manuallySelected.has(employeeNo);
            const row = checkbox.closest("tr");
            if (row) {
                row.classList.toggle("is-selected", checkbox.checked);
            }
        });

        if (selectAllTargets) {
            selectAllTargets.checked = (
                selectionScopeCount > 0
                && currentSelectedCount === selectionScopeCount
            );
            selectAllTargets.indeterminate = (
                currentSelectedCount > 0
                && currentSelectedCount < selectionScopeCount
            );
        }

        synchronizePayload();
        grantButton.disabled = !(
            serverReady
            && currentSelectedCount > 0
            && confirmAll.checked
        );
    }

    /** 개별 행의 체크 변경을 현재 선택 모드의 포함 또는 제외 목록에 반영합니다. */
    function handleTargetChange(event) {
        const checkbox = event.currentTarget;
        if (allSelected) {
            if (checkbox.checked) {
                excludedFromAll.delete(checkbox.value);
            } else {
                excludedFromAll.add(checkbox.value);
            }
        } else if (checkbox.checked) {
            manuallySelected.add(checkbox.value);
        } else {
            manuallySelected.delete(checkbox.value);
        }
        updateSelectionState();
    }

    /** 전체 선택 시 예외를 초기화하고, 전체 해제 시 모든 선택 상태를 비웁니다. */
    function handleSelectAllChange() {
        allSelected = selectAllTargets.checked;
        manuallySelected.clear();
        excludedFromAll.clear();
        updateSelectionState();
    }

    targetSelectors.forEach((checkbox) => {
        checkbox.addEventListener("change", handleTargetChange);
    });
    if (selectAllTargets) {
        selectAllTargets.addEventListener("change", handleSelectAllChange);
    }
    confirmAll.addEventListener("change", updateSelectionState);
    updateSelectionState();
})();
