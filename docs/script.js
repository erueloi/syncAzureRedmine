document.addEventListener('DOMContentLoaded', function() {
  fetch('data.json')
      .then(response => response.json())
      .then(data => {
          const tbody = document.querySelector('tbody');
          data.forEach(execution => {
              const row = document.createElement('tr');
              row.innerHTML = `
                  <td>${execution.fechaHora}</td>
                  <td>${execution.tareasCreadas}</td>
                  <td>${execution.tareasModificadas}</td>
                  <td>${execution.estado}</td>
                  <td><a href="${execution.informe}" target="_blank">Informe</a></td>
              `;
              tbody.appendChild(row);
          });
      })
      .catch(error => console.error('Error al cargar los datos: ', error));
});