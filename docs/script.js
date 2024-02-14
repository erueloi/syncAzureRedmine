// Función para cargar o recargar los datos
function loadData() {
  fetch('data.json')
    .then(response => response.json())
    .then(data => {
      const tbody = document.querySelector('tbody');
      tbody.innerHTML = ''; // Limpiar la tabla antes de volver a llenarla
      let ultimaActualizacion = '';

      if (data.length > 0) {
        // Asumiendo que el primer elemento es la ejecución más reciente
        ultimaActualizacion = data[0].fechaHora;
      }

      data.forEach(execution => {
        const row = document.createElement('tr');
        row.innerHTML = `
          <td>${execution.fechaHora}</td>
          <td>${execution.tareasCreadas}</td>
          <td>${execution.tareasModificadas}</td>
          <td>${execution.tareasFallidas}</td>
          <td>${execution.tareasNoModificadas}</td>
          <td>${execution.estado}</td>
          <td><a href="${execution.detalle}">Detalle</a></td>
        `;
        tbody.appendChild(row);
      });

      document.getElementById('last-update').textContent = ultimaActualizacion;
    })
    .catch(error => console.error('Error al cargar los datos: ', error));
}

document.addEventListener('DOMContentLoaded', function() {
  loadData();

  // Añadir event listener al botón de refresco
  document.getElementById('refresh-button').addEventListener('click', loadData);

  // Configurar la auto-actualización cada 15 minutos (900000 milisegundos)
  setInterval(loadData, 900000);
});
