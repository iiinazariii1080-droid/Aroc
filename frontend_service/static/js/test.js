render(h) {
    let dataVAttr = { "data-v-3c7ad43f": "" };
    let busyAny = this.isLoading || this.isSaving || this.isDeleting;
    
    return h("div", { class: ["script-panel"], attrs: dataVAttr }, [
      h("div", { class: "panels", attrs: dataVAttr }, [
        // Левая часть (dropdown)
        h("div", { class: "left-panel", attrs: dataVAttr }, [
          h(
            "label",
            { class: "label-select", attrs: { for: "section-select", ...dataVAttr } },
            "Choose a job:"
          ),
          // Выпадающий список
          h(
            "select",
            {
              class: "section-select",
              attrs: { id: "section-select", ...dataVAttr },
              domProps: { value: this.selectedSection },
              on: {
                change: (event) => {
                  this.selectedSection = event.target.value;
                }
              }
            },
            // Строим <option> для каждого объекта из this.jobs
            this.jobs.map((job) =>
              h(
                "option",
                { attrs: { ...dataVAttr, value: job.name } },
                job.name
              )
            )
          ),

          // Кнопка "Change"
          h(
            "button",
            {
              class: "start-script-button",
              style: {
                backgroundColor: "#3662EC",
                color: "white",
                opacity: this.isScriptRunning ? 0.6 : 1
              },
              attrs: {
                ...dataVAttr,
                disabled: this.isScriptRunning
              },
              on: { click: this.handleStartScript }
            },
            "Change"
          )
        ])
      ])
    ]);
  }