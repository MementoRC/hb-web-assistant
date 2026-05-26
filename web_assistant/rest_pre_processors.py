import abc

from web_assistant.connections.data_types import RESTRequest


class RESTPreProcessorBase(abc.ABC):
    """An interface class that enables functionality injection into the `RESTAssistant`.

    The logic provided by a class implementing this interface is applied to a request
    before it is sent out to the server.
    """

    @abc.abstractmethod
    async def pre_process(self, request: RESTRequest) -> RESTRequest:
        """Transform the request before it is dispatched to the server.

        Implementations must return the (potentially mutated) request object.
        """
        raise NotImplementedError
